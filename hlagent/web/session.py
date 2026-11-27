"""Run each browser's synchronous backend in its own local worker process."""


import multiprocessing
import json
import os
from pathlib import Path
import signal
from datetime import datetime, timezone
from threading import Event, Lock, RLock, Thread
from time import monotonic

from hlagent.paths import ROOT, new_run_id


class ExecutionCancellation:
    """Interrupt only this worker's approved tool execution, once per request."""

    def __init__(self, event):
        self.event = event
        self.active = False
        self.phase = None
        self.signalled = False
        self.consumed = False

    def begin(self, active):
        self.active, self.phase, self.signalled = active, None, False
        self.consumed = False

    def checkpoint(self, phase):
        self.phase = None
        if self.active and self.event.is_set():
            self.active = False
            self.consumed = True
            self.event.clear()
            raise KeyboardInterrupt
        self.phase = phase

    def interrupt(self, signum, frame):
        # Signal handlers must not acquire multiprocessing Event locks.
        if self.active and self.phase == 'execution' and self.signalled:
            self.active = False
            self.consumed = True
            raise KeyboardInterrupt

    def monitor(self):
        if self.active and self.phase == 'execution' and self.event.is_set() and not self.signalled:
            self.signalled = True
            os.kill(os.getpid(), signal.SIGINT)

    def finish(self):
        self.active = False
        self.event.clear()


def worker(connection, root, seccomp, cancel_event):
    from hlagent.runtime.execution_feedback import ToolProgress
    from hlagent.web.backend import WebBackend
    backend = None
    send_lock = Lock()
    cancellation = ExecutionCancellation(cancel_event)
    signal.signal(signal.SIGINT, cancellation.interrupt)

    def send(event):
        with send_lock:
            connection.send(event)

    try:
        backend = WebBackend(root, seccomp_unconfined=seccomp)
        send({'kind': 'state', 'state': backend.snapshot()})
        while True:
            request = connection.recv()
            if request['operation'] == 'close':
                break
            kind, plan = backend.approval
            cancellation.begin(request['operation'] == 'approve' and backend.snapshot()['can_cancel'])
            stop = Event()
            progress_lock = Lock()
            tools = [None]

            def progress(stage, plan):
                cancellation.checkpoint(stage)
                directory = Path(plan['run_dir']) if isinstance(plan, dict) else Path(plan.run_dir)
                with progress_lock:
                    if tools[0] is None or tools[0].directory != directory:
                        tools[0] = ToolProgress(plan) if not isinstance(plan, dict) else None
                send({'kind': 'progress', 'stage': backend.redact(stage), 'run_dir': str(directory)})

            def monitor():
                while not stop.wait(1):
                    cancellation.monitor()
                    with progress_lock:
                        messages = tools[0].read() if tools[0] is not None else []
                    for message in messages:
                        send({'kind': 'progress', 'stage': backend.redact(message)})

            thread = Thread(target=monitor, daemon=True)
            thread.start()
            try:
                state = backend.handle(request, progress)
                cancellation.active = False
                if cancel_event.is_set() and not cancellation.consumed:
                    state['message'] += '\nThe task finished before the stop request took effect.'
                send({'kind': 'state', 'state': state,
                      'user_message': backend.redact(request.get('text', ''))})
            except KeyboardInterrupt:
                send({'kind': 'state', 'state': backend.cancelled_preparation(kind, plan)})
            except Exception as error:
                # No raw traceback, config objects or exception repr reaches the browser.
                send({'kind': 'error', 'message': backend.redact(str(error)), 'state': backend.snapshot()})
            finally:
                cancellation.finish()
                stop.set()
                thread.join()
    except (EOFError, BrokenPipeError, OSError):
        pass
    except Exception:
        send({'kind': 'error', 'message': 'Could not initialize the session. Check server configuration.'})
    finally:
        connection.close()


class WebSession:
    """Keep execution independent of Streamlit reruns. Never replay a queued command."""

    def __init__(self, root=None, seccomp=False):
        self.root = Path(root) if root is not None else ROOT / 'runs/web-sessions' / new_run_id('session', ROOT / 'runs/web-sessions')
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        context = multiprocessing.get_context('spawn')
        self.connection, child = context.Pipe()
        self.cancel_event = context.Event()
        self.process = context.Process(target=worker, args=(child, self.root, seccomp, self.cancel_event), daemon=True)
        self.state = None
        self.error = None
        self.history = []
        self.progress = []
        self.busy = True
        self.closed = False
        self.cancellable = False
        self.cancel_requested = False
        self.request_id = 0
        self.lock = RLock()
        self.started = monotonic()
        self.process.start()
        child.close()

    def poll(self):
        # Refreshed pages and status fragments may read the same worker.
        with self.lock:
            if self.closed:
                return
            self._poll()

    def _poll(self):
        while self.connection.poll():
            try:
                event = self.connection.recv()
            except EOFError:
                break
            if event['kind'] == 'progress':
                self.progress.append(event)
                self.progress = self.progress[-100:]
            else:
                self.busy = False
                self.cancellable = False
                self.cancel_requested = False
                if event.get('state') is not None:
                    self.state = event['state']
                    if event.get('user_message'):
                        self.history.append({'role': 'user', 'content': event['user_message']})
                    message = self.state['message']
                    if message:
                        self.history.append({'role': 'assistant', 'agent': self.state['role'], 'content': message})
                self.error = event.get('message') if event['kind'] == 'error' else None
        if not self.process.is_alive():
            self.busy = False
            self.error = 'The session worker stopped. Inspect saved runs before starting another task. No automatic retry was made.'

    def submit(self, request):
        with self.lock:
            self.poll()
            if self.busy or self.closed or not self.process.is_alive():
                raise ValueError('A request is already running or this session has ended.')
            # Chat text is displayed only after the worker has screened it for keys.
            self.error = None
            self.progress = []
            self.cancel_event.clear()
            self.cancel_requested = False
            self.request_id += 1
            self.cancellable = request['operation'] == 'approve' and bool(self.state and self.state.get('can_cancel'))
            self.started = monotonic()
            self.busy = True
            self.connection.send(request)

    def cancel(self, expected_request):
        with self.lock:
            self.poll()
            if expected_request != self.request_id or not self.busy or not self.cancellable:
                raise ValueError('This analysis is no longer running. No stop request was sent.')
            if self.cancel_requested:
                return
            entry = {'request_id': self.request_id, 'requested_at': datetime.now(timezone.utc).isoformat(),
                     'run_dir': next((item['run_dir'] for item in reversed(self.progress) if item.get('run_dir')), None)}
            with (self.root / 'cancellation-requests.jsonl').open('a') as stream:
                stream.write(json.dumps(entry) + '\n')
            self.cancel_requested = True
            self.cancel_event.set()

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.poll()
            if self.busy:
                raise ValueError('Wait for the current request to finish. Closing the page does not stop Docker.')
            self.closed = True
            if self.process.is_alive():
                self.connection.send({'operation': 'close'})
                self.process.join(timeout=2)
            self.connection.close()
