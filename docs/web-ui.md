# Web guide

Use the Web interface to prepare and approve analyses, follow execution and
browse saved results. For installation, see [Getting Started](../README.md#getting-started).

## Start the Web interface

From the HLAgent directory, activate the environment and launch Web:

```bash
conda activate hlagent
python -m streamlit run web.py
```

The default browser opens automatically. If needed, open the address shown in
the terminal; the default local address is `http://127.0.0.1:8501`.

## Prepare and approve an analysis

1. Select a session or use `New session` in the sidebar.
2. In `Chat & analysis`, choose a task or describe your goal.
3. Follow the prompts to select recorded inputs or enter file paths and settings.
4. Review the plan. Expand `Full plan information` for the full description.
5. Use the approval button to start, or `Cancel plan` to discard it.

The sidebar shows your current role, step and model. Use `Change inputs / settings`
where available to revise a plan, then review and approve the updated version.
Typing `y` in the conversation does not approve an analysis.

Paths refer to files on the computer running HLAgent. Browser uploads and a
general file manager are not provided. See the [analysis guide](usage.md#prepare-your-inputs)
for input formats.

## Change your model

Open `Model settings` in the sidebar:

1. Choose the provider and model. Switching provider fills in an editable
   suggestion or its previously configured model.
2. Enter an API key in the password field if required.
3. Select `Save settings`.

Saved credentials can be reused across sessions. For Ollama, HLAgent can start
the local service when needed, but the selected model must already be installed.
To download the default model, run `ollama pull llama3.1:8b` in a terminal.

## Use HLA Tutor

Choose `HLA Tutor`, then select the answer source under `Answer sources`.

- `local`: HLAgent usage and implementation notes.
- `rag`: indexed documents, including the bundled CookHLA and HATK papers after setup.
- `web`: Web search through Tavily.
- `hybrid`: indexed documents and Tavily search.

Before selecting `web` or `hybrid`, open `Search settings` in this area and
configure a separate Tavily API key. Neither `local` nor `rag` needs that key.
For document indexing and source details, see the [Tutor guide](tutor.md).

## Follow or stop execution

During approved Docker analyses, execution status and recognized tool-log updates
appear outside the collapsible `Conversation` area. The UI shows reported stages
rather than an estimated completion percentage.

To interrupt an analysis, select `Stop analysis` and confirm. Check the recorded
outcome before starting another execution. Closing the browser does not cancel
an analysis. Duplicate submissions are blocked while the session is busy.

## Results and reports

Open `Results & reports` in the sidebar and choose an action:

| Action | How to use it |
| --- | --- |
| `View reports` | Select a saved report to view or download. |
| `Create report` | Select analyses, click `Review selection`, review the plan and approve report creation. |
| `Analysis files` | Select a saved analysis and inspect its result files. |

Use `Include previous Web sessions` to list saved results from other Web sessions.
Use `Include CLI results and reports` to include runs created through CLI.
Cancel a pending report plan before changing these filters.

Reports provide figure previews and PDF/table downloads. You can also open a
standalone report's `index.html` from disk. Report creation reads saved outputs
without rerunning analyses or calling an LLM.

Report and analysis deletion require separate confirmation. Analysis deletion
is permanent and may break references from later analyses; a standalone report
can retain its copied artifacts.

## Manage sessions

| Action | What happens |
| --- | --- |
| `New session` | Starts another conversation and keeps previous sessions in the selector. |
| Select a previous session | Returns to its conversation and pending plan. |
| Refresh the same workspace URL | Reconnects to the workspace while the server is running. |
| `Delete session` | Requires confirmation; removes the conversation but keeps saved analysis files. |
| Restart the Web server | Loses live conversations and pending plans; saved analyses and reports remain on disk. |

Busy sessions cannot be switched or deleted. After a server restart, use
`Include previous Web sessions` to find saved results.

## Advanced behavior

- The interface is intended for local or trusted research use. It has no public-hosting authentication.
- A workspace URL gives access to that workspace on this interface; it is not an account or filesystem permission system.
- Each session has a separate worker, conversation, inputs and approvals.
- Saved API keys are not sent back to the browser for display or included in LLM conversation text.
- Exported environment variables override saved settings on startup.
- Web reuses CLI task plans, validators and analysis runners. It adds no separate scientific analysis methods.
