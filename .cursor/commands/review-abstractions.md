Review the current diff for unnecessary abstraction.

Do not edit files.

Look specifically for:
- one-call helper functions
- pass-through wrappers
- classes with one method and no meaningful state
- factories with only one concrete implementation
- protocols/interfaces with only one implementation
- registries used in only one place
- adapters that only rename fields or forward calls
- generic names like Manager, Handler, Processor, Executor, Orchestrator
- config objects that only mirror function arguments
- layers that make tracing harder without reducing duplication

For each suspicious abstraction, report:
1. File/path
2. Abstraction name
3. Why it may be unnecessary
4. Whether to inline, keep, rename, or simplify
5. What risk simplification would introduce

Also identify abstractions that are justified and should be kept.
