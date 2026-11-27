"""Reconnect refreshed browser pages to existing server-owned session workers."""

from secrets import token_hex
from threading import RLock


class Workspace:
    def __init__(self, session):
        self.sessions = [session]
        self.labels = ['Session 1']
        self.next_number = 2
        self.index = 0
        self.page = 'Chat & analysis'
        self.lock = RLock()

    @property
    def current(self):
        with self.lock:
            return self.sessions[self.index]

    def add(self, factory):
        with self.lock:
            self.current.poll()
            if self.current.busy:
                raise ValueError('Wait for the current request to finish.')
            self.sessions.append(factory())
            self.labels.append(f'Session {self.next_number}')
            self.next_number += 1
            self.index = len(self.sessions) - 1
            self.page = 'Chat & analysis'

    def select(self, index):
        with self.lock:
            self.current.poll()
            if self.current.busy:
                raise ValueError('Wait for the current request to finish.')
            self.index = index

    def remove(self, expected, factory):
        with self.lock:
            if self.current is not expected:
                raise ValueError('The selected session changed. Review the deletion again.')
            expected.poll()
            if expected.busy:
                raise ValueError('Wait for the current request to finish.')
            expected.close()
            self.sessions.pop(self.index)
            self.labels.pop(self.index)
            if not self.sessions:
                self.sessions.append(factory())
                self.labels.append(f'Session {self.next_number}')
                self.next_number += 1
            self.index = min(self.index, len(self.sessions) - 1)
            self.page = 'Chat & analysis'


class SessionRegistry:
    def __init__(self):
        self.workspaces = {}
        self.lock = RLock()

    def connect(self, token, factory):
        with self.lock:
            if token not in self.workspaces:
                token = token_hex(24)
                self.workspaces[token] = Workspace(factory())
            return token, self.workspaces[token]
