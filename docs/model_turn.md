# Stateless model-turn contract

`retrieval_arena/model_turn.py` builds one function-call request from the caller's visible history and allowed actions. It does not decide which action to take, retain history, choose source pages, or validate answer citations; those remain caller responsibilities.

The reusable `read` and `follow` schemas require a positive `start_line` and `line_count` from 1 through 80. `validate_action` checks the same range after a response. This bound matches the ICSC documentation caller's line-range limit and prevents oversize reads from consuming a turn. The caller still admits only pages offered by search or by a previously observed outgoing link. Prompt text can explain these rules but is not their enforcement point.

The source hash, prompt, model/provider, corpus and budgets belong in each experiment's frozen manifest. A changed schema changes the request body and must be pinned as a new run rather than compared as an unchanged rerun.
