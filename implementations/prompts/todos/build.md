### TODO Discipline (Execution)

- Before modifying files or running commands, mark the relevant todo `in_progress` with `todo.update`.
- After completing a unit of work, attach evidence (files, tests) and mark the todo `done` with `todo.complete`.
- Use `todo.note` for blockers or follow-ups; cancel items that are no longer required.
- Do **not** call `mark_task_complete()` while any todo remains in `todo`, `in_progress`, or `blocked` status.
