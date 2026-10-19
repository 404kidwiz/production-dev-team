# Adaptive discovery

One question per turn. Before asking, check the repository, existing docs and earlier answers; ask only what they cannot settle. Give a recommended answer and say what the answer changes. Record each answer and its unresolved implications. Stop when the decisions needed for the next safe slice are resolved, not when every hypothetical has been discussed.

The queue below is ordered by dependency. Each line is ONE question; the indented follow-ups are separate later turns, asked only if the answer makes them relevant. After each answer, note the matching triggers. Record them with `ledger.py triggers` only once a ledger exists (it needs write permission; see SKILL.md); until then keep the list in the conversation.

1. **Purpose.** "What should a person be able to do with this, and who is it for?"
   - Then: "What would make a first release useful enough to try?"
   - Then (if unclear from the repo): "Is this a new app or an existing repository?"
2. **Sign-in.** "Will people need to sign in?" (`accounts`)
   - If yes: "Can more than one company or team use it, with separate data?" (`multitenant`)
   - If yes: "Who may see, change, export, delete or administer each kind of record?"
   - Only if raised: "Does support really need to act as a user?" (see `N-SUPPORT-ACCESS`)
3. **Data.** "What is the most sensitive thing it will store or send elsewhere?" (identity, health, payment, location, children's data, uploads, prompts) (`privacy`, `regulated`, `children`, `uploads`)
   - Then: "Which countries or customer contracts apply?"
   - Then: "Who owns the data?"
4. **Money.** "Will customers pay?" (`payments`)
   - If yes: "One-time, subscription or usage-based?" Then currencies, refunds, tax and invoices as separate turns. Use payment-provider tokenization, never raw card collection.
5. **Services.** "Which outside services must it depend on?" (email, AI, calendar, third-party APIs, imports) (`integrations`, `ai`, `email`, `jobs`)
   - Then: "What should happen when one of them is down or a call is retried?"
6. **Budget.** "What is the monthly operating budget?"
   - Then: "How many users do you expect at first?" (`scale`)
   - Then: "How much data loss or downtime could you accept?" Translate the answer into measurable recovery and service objectives, not guesses. (`ops`)
7. **People.** "Which devices and connection speeds matter most?" (`frontend`)
   - Then: "Are there accessibility or language requirements?"
   - Then: "Which journey must never fail silently?"
8. **Ownership.** "Who owns the domain, hosting, repository and vendor accounts?" (`ops`, `team`)
   - Then: "Who approves a release and receives alerts?" (see `N-INCIDENT`)
   - Then: "Will other staff run or support it, or does a contract promise uptime, a status page or on-call?" (`org`)
   - Then: "Who can restore data and keep dependencies current?"

## Reuse before rediscovery

For an existing project, reuse the approved brief, decisions and acceptance criteria. Reopen only a decision invalidated by changed requirements, new evidence or an expired assumption; age alone is not a reason. For substantial work, summarize who benefits, the observable outcome and relevant failure/recovery behavior in the existing plan or response. See `references/helpful-execution.md`; do not create a second plan or restart this question queue when the answers are already settled.

## Beginner decision card

Decision: [plain-language purpose]
A: [appropriate option, current official source, pricing date, workload assumptions, limits]
B: [credible alternative with equivalent details]
Recommend: [one, why it fits this user's requirements]
Tradeoff: [maintenance, privacy, portability, growth cost]
Approval needed: [specific consequential action, not blanket consent]
Unverified: [pricing or capability unknowns if browsing unavailable]

Do not make founders specify table indexes or encryption algorithms; derive these from risk and demonstrate the tests. Never present a free tier as an unlimited production guarantee.
