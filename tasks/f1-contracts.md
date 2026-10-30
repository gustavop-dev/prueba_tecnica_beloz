# F1 Contracts — Money, Ledger, Models

> Written by the orchestrator. Each app subagent receives the **Common conventions** plus its own section. F2 flows are built on exactly these interfaces; changing a signature requires the orchestrator.

## 1. Common conventions (every app)

### Code layout

| File | Content |
| --- | --- |
| `<app>/models.py` | Models. Never imported outside the app (import-linter DEP-2). |
| `<app>/migrations/` | `0001_initial.py` plus any `RunSQL` migration for database guards. |
| `<app>/states.py` | State machine: allowed transitions table and `assert_transition`. |
| `<app>/services.py` | **Public API**: service functions, DTOs (frozen dataclasses) and exceptions. Everything other code needs is importable from here. |
| `<app>/tests/` | Tests for this app only (pytest, real PostgreSQL). Fixtures in `<app>/tests/conftest.py`. |

### Models

- Primary keys: `id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)`.
- Cross-app references are plain `UUIDField`s named `<thing>_id`, never `ForeignKey` (MOD-3).
- Money: `models.BigIntegerField` with the `_minor` suffix. **Never `FloatField` or `DecimalField`** (BR-MONEY-3).
- Statuses: `models.TextChoices` stored in a `CharField`, plus a `CheckConstraint` restricting the column to the declared values.
- Constraints use Django 6.1 syntax: `models.CheckConstraint(condition=Q(...), name="...")`.
- Timestamps: `created_at = DateTimeField(auto_now_add=True)`; state timestamps (`confirmed_at`, `closed_at`, …) are nullable and set by the service.
- Database guards beyond `CheckConstraint` (triggers) are created with `migrations.RunSQL(sql, reverse_sql)` in PL/pgSQL.

### Services

- DTOs: `@dataclass(frozen=True, slots=True)`. Services **never** return model instances (DEP-3).
- Every mutating service requires an open transaction and fails otherwise:
  `if not transaction.get_connection().in_atomic_block: raise RuntimeError("<function> must run inside transaction.atomic")`.
  Transactions are opened by the caller (the operations core), never by the app.
- `lock_*` functions use `select_for_update()` and are the only way to read a row that is about to be mutated (CON-2, CON-3).
- Amount arguments are validated again inside the service (defense in depth): must be `int`, not `bool`, and `> 0` unless stated otherwise. Raise `ValueError` for programming errors; raise the app's domain exceptions for business rejections.
- Domain exceptions: one base class per app (`<App>Error(Exception)`) and specific subclasses listed in each section. The operations core maps them to API error codes in F2.
- Invalid transitions raise the app's `InvalidTransition(<App>Error)` with `from_status` and `to_status` attributes, and leave the row unchanged.

### State machine module (`states.py`)

```python
TRANSITIONS: dict[Status, frozenset[Status]] = {...}  # exactly the table in docs/state-machines.md

def assert_transition(current: Status, target: Status) -> None:
    """Raise InvalidTransition when target is not in TRANSITIONS[current]."""
```

### Tests (from `.claude/skills/backend-test-coverage/SKILL.md`)

- Three-part definition of done: real behavior; concrete observable value; docstring names the bug it catches **and cites the spec ID** (e.g. `"""Fails if a closed account accepts a new debit (BR-CLO-6)."""`).
- No `if`/loops in a test body: use `pytest.mark.parametrize`.
- `@pytest.mark.django_db` by default; `transaction=True` only when the test needs a real commit.
- **TEST-SM-1:** one parametrized test generated from `TRANSITIONS`: every `(from, to)` pair of statuses **not** allowed must raise `InvalidTransition` and leave the stored status unchanged. Also test every allowed transition succeeds.
- Database guards are tested with raw SQL through `django.db.connection.cursor()`, bypassing Python, asserting `django.db.utils.IntegrityError` / `InternalError` / `ProgrammingError` as appropriate.
- To test a deferred constraint trigger inside a test transaction, run `SET CONSTRAINTS ALL IMMEDIATE` before the offending statement.

### Commands

Run from the worktree root (Docker and make are the only host requirements; each worktree gets its own PostgreSQL container):

```bash
make test ARGS="<app>"   # only this app's tests
make lint                # ruff + format check + import-linter
make format              # if ruff format reports changes
```

Before finishing: `make lint` and `make test` (full suite) must be green.

## 2. `ledger` — LED-1..6, CACHE-*, ledger.md

### Models

**`LedgerAccount`**

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID PK | |
| `code` | `TextChoices`: `CASH`, `MERCHANT_PAYABLE`, `CUSTOMER_COLLATERAL`, `CUSTOMER_RECEIVABLE` | |
| `kind` | `TextChoices`: `ASSET`, `LIABILITY` | `CASH`, `CUSTOMER_RECEIVABLE` → asset; `MERCHANT_PAYABLE`, `CUSTOMER_COLLATERAL` → liability. |
| `credit_account_id` | UUID, nullable | `NULL` for the two global accounts. |

Constraints: code/kind consistency; global codes ⇔ `credit_account_id IS NULL`; unique `code` among globals (partial unique); unique `(credit_account_id, code)`.

Data migration: create the two global accounts (`CASH`, `MERCHANT_PAYABLE`).

**`JournalEntry`**: `id` UUID PK; `kind` `TextChoices` (`DEPOSIT_CONFIRMED`, `PURCHASE`, `REPAYMENT`, `CLOSURE`); `credit_account_id` UUID (the credit account the entry concerns); `reference_id` UUID nullable (deposit id, authorization id…); `created_at`.

**`JournalLine`**: `id` UUID PK; `entry` FK → `JournalEntry` (`PROTECT`, `related_name="lines"`); `ledger_account` FK → `LedgerAccount` (`PROTECT`); `amount_minor` `BigIntegerField`, **signed**: debit positive, credit negative (LED-5); `CHECK amount_minor <> 0`.

### Database guards

| ID | Guard |
| --- | --- |
| LED-4 | `BEFORE UPDATE OR DELETE ... FOR EACH ROW` triggers on entries and lines that always raise. `BEFORE TRUNCATE ... FOR EACH STATEMENT` triggers that raise **unless the current role is a superuser** (`SELECT rolsuper FROM pg_roles WHERE rolname = current_user`). A superuser can disable any trigger anyway; the exemption lets Django's test flush run, while the application role in production (non-superuser) can never truncate. |
| LED-6 | Deferred constraint triggers (`CREATE CONSTRAINT TRIGGER ... DEFERRABLE INITIALLY DEFERRED`): on `JournalLine` insert, the entry's lines must sum to 0; on `JournalEntry` insert, the entry must have at least 2 lines and sum to 0. |

### Services

```python
@dataclass(frozen=True, slots=True)
class LineDTO: ledger_account_code: str; amount_minor: int
@dataclass(frozen=True, slots=True)
class EntryDTO: id: UUID; kind: str; credit_account_id: UUID; reference_id: UUID | None; created_at: datetime; lines: tuple[LineDTO, ...]
@dataclass(frozen=True, slots=True)
class CustomerBalancesDTO: collateral_minor: int; receivable_minor: int   # normal-balance positive

def provision_customer_accounts(credit_account_id: UUID) -> None
def post_deposit_confirmed(credit_account_id: UUID, amount_minor: int, reference_id: UUID) -> EntryDTO
    # Dr CASH / Cr CUSTOMER_COLLATERAL
def post_purchase(credit_account_id: UUID, amount_minor: int, reference_id: UUID) -> EntryDTO
    # Dr CUSTOMER_RECEIVABLE / Cr MERCHANT_PAYABLE
def post_repayment(credit_account_id: UUID, amount_minor: int, reference_id: UUID) -> EntryDTO
    # Dr CASH / Cr CUSTOMER_RECEIVABLE
def post_closure(credit_account_id: UUID, debt_minor: int, refund_minor: int, reference_id: UUID) -> EntryDTO
    # one entry: Dr CUSTOMER_COLLATERAL / Cr CUSTOMER_RECEIVABLE (debt_minor) and
    # Dr CUSTOMER_COLLATERAL / Cr CASH (refund_minor); a pair whose amount is 0 is not written;
    # debt_minor >= 0, refund_minor >= 0, debt_minor + refund_minor > 0
def get_customer_balances(credit_account_id: UUID) -> CustomerBalancesDTO
def list_entries(credit_account_id: UUID) -> list[EntryDTO]       # chronological, lines included
def find_unbalanced_entry_ids() -> list[UUID]                      # for reconcile
```

Exceptions: `LedgerError`, `LedgerAccountsNotProvisioned`.

### Mandatory tests

TEST-DB-1 (update/delete always rejected; truncate rejected for a non-superuser role created inside the test with `SET LOCAL ROLE`), TEST-DB-3 (unbalanced entry and single-line entry rejected), each posting writes exactly the lines of ledger.md §5 with the right signs, closure skips zero pairs, balances follow the expected-balance table.

## 3. `accounts` — BR-LIM, BR-PUR-2, BR-PAY, BR-CLO, CACHE-3

### Model `CreditAccount`

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID PK | Set by the caller to the reserved id (no default generation at open). |
| `owner_id` | UUID | Customer principal id. |
| `deposit_id` | UUID, unique | |
| `status` | `TextChoices`: `ACTIVE`, `CLOSED` | |
| `collateral_ratio_bps` | `PositiveIntegerField` | Ratio used to compute the limit. |
| `credit_limit_minor` | `BigIntegerField` | |
| `outstanding_minor` | `BigIntegerField`, default 0 | Cache of the receivable balance. |
| `created_at`, `closed_at` | datetime / nullable datetime | |

Constraints: CACHE-3 `0 <= outstanding_minor <= credit_limit_minor`; `credit_limit_minor >= 0`; `collateral_ratio_bps > 0`; `status = 'CLOSED'` ⇔ `closed_at IS NOT NULL`; `status = 'CLOSED'` ⇒ `outstanding_minor = 0` (BR-CLO-5).

### State machine

`ACTIVE → CLOSED`. Nothing else.

### Services

```python
@dataclass(frozen=True, slots=True)
class AccountDTO:
    id: UUID; owner_id: UUID; deposit_id: UUID; status: str; collateral_ratio_bps: int
    credit_limit_minor: int; outstanding_minor: int; created_at: datetime; closed_at: datetime | None
    @property
    def available_credit_minor(self) -> int: ...   # credit_limit_minor - outstanding_minor (BR-LIM-5)

@dataclass(frozen=True, slots=True)
class ClosureDTO: account: AccountDTO; debt_at_closure_minor: int

def open_account(*, account_id: UUID, owner_id: UUID, deposit_id: UUID, credit_limit_minor: int, collateral_ratio_bps: int) -> AccountDTO
def lock_account(account_id: UUID) -> AccountDTO                      # SELECT ... FOR UPDATE; AccountNotFound
def get_account(account_id: UUID) -> AccountDTO                       # AccountNotFound
def get_account_for_owner(account_id: UUID, owner_id: UUID) -> AccountDTO
    # AccountNotFound both when it does not exist and when owner differs (SEC-IDOR-1/2)
def increase_outstanding(account_id: UUID, amount_minor: int) -> AccountDTO
    # under lock; AccountClosed if CLOSED; InsufficientCredit if amount > available (BR-PUR-2)
def decrease_outstanding(account_id: UUID, amount_minor: int) -> AccountDTO
    # under lock; AccountClosed; PaymentExceedsDebt if amount > outstanding (BR-PAY-2/3)
def close_account(account_id: UUID) -> ClosureDTO
    # under lock; AccountClosed if already closed; captures outstanding as debt_at_closure,
    # sets outstanding 0, status CLOSED, closed_at now (BR-CLO-5)
def iter_accounts() -> Iterator[AccountDTO]                          # for reconcile
```

Exceptions: `AccountsError`, `AccountNotFound`, `AccountClosed`, `InsufficientCredit`, `PaymentExceedsDebt`, `InvalidTransition`.

### Mandatory tests

TEST-SM-1, TEST-DB-2 (raw SQL setting outstanding below 0 or above the limit is rejected; closed with outstanding > 0 rejected), every service rejection with the exact exception, IDOR lookup returns `AccountNotFound` for another owner, mutating services refuse to run outside `transaction.atomic`.

## 4. `collateral` — BR-DEP, BR-CLO-1..4, state-machines.md §1

### Model `CollateralDeposit`

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID PK | |
| `owner_id` | UUID | |
| `reserved_account_id` | UUID, unique | Generated at creation; becomes the credit account id on confirmation. |
| `amount_minor` | `BigIntegerField` | `CHECK > 0`. |
| `status` | `TextChoices`: `PENDING`, `CONFIRMED`, `FAILED`, `RELEASED`, `PARTIALLY_APPLIED`, `APPLIED` | |
| `applied_minor`, `refunded_minor` | nullable `BigIntegerField` | Set only at settlement. |
| `created_at`, `confirmed_at`, `failed_at`, `settled_at` | datetimes | |

Constraints (settlement consistency, BR-CLO-1..4): settled statuses ⇔ `applied_minor` and `refunded_minor` not null; `applied_minor + refunded_minor = amount_minor`; `RELEASED` ⇒ `applied_minor = 0`; `APPLIED` ⇒ `refunded_minor = 0`; `PARTIALLY_APPLIED` ⇒ `0 < applied_minor < amount_minor`.

### State machine

Exactly docs/state-machines.md §1.

### Services

```python
@dataclass(frozen=True, slots=True)
class DepositDTO:
    id: UUID; owner_id: UUID; reserved_account_id: UUID; amount_minor: int; status: str
    applied_minor: int | None; refunded_minor: int | None
    created_at: datetime; confirmed_at: datetime | None; failed_at: datetime | None; settled_at: datetime | None

def create_pending_deposit(*, owner_id: UUID, amount_minor: int) -> DepositDTO
def lock_deposit(deposit_id: UUID) -> DepositDTO                       # FOR UPDATE; DepositNotFound
def get_deposit(deposit_id: UUID) -> DepositDTO
def get_deposit_for_owner(deposit_id: UUID, owner_id: UUID) -> DepositDTO   # DepositNotFound for other owners
def confirm_deposit(deposit_id: UUID) -> DepositDTO                    # PENDING → CONFIRMED; DepositNotPending
def fail_deposit(deposit_id: UUID) -> DepositDTO                       # PENDING → FAILED; DepositNotPending
def settle_on_closure(deposit_id: UUID, debt_at_closure_minor: int) -> DepositDTO
    # CONFIRMED → RELEASED | PARTIALLY_APPLIED | APPLIED by the guards of state-machines.md;
    # applied = debt, refunded = amount - debt; ValueError if debt < 0 or debt > amount
```

Exceptions: `CollateralError`, `DepositNotFound`, `DepositNotPending`, `InvalidTransition`.

### Mandatory tests

TEST-SM-1, the three settlement outcomes with boundary values (debt 0, 1, amount − 1, amount), raw-SQL violations of the settlement constraints rejected, amount ≤ 0 rejected by the database.

## 5. `cards` — SEC-CARD-1..3, state-machines.md §3

### Model `Card`

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID PK | |
| `credit_account_id` | UUID, unique | One card per account (BR-ACC-3). |
| `token` | `CharField`, unique | Internal opaque token from `secrets` (e.g. `"card_" + secrets.token_urlsafe(24)`). Not a PAN. |
| `last4` | `CharField(4)` | `CHECK last4 ~ '^[0-9]{4}$'`. Random digits from `secrets`. |
| `status` | `TextChoices`: `ACTIVE`, `INACTIVE`, `CLOSED` | |
| `created_at`, `closed_at` | | `CLOSED` ⇔ `closed_at IS NOT NULL`. |

**No field may hold a full card number** (SEC-CARD-1).

### State machine

Exactly docs/state-machines.md §3 (`INACTIVE` = frozen, reversible).

### Services

```python
@dataclass(frozen=True, slots=True)
class CardDTO:   # never includes the token (SEC-CARD-3)
    id: UUID; credit_account_id: UUID; last4: str; status: str; created_at: datetime; closed_at: datetime | None

def issue_card(credit_account_id: UUID) -> CardDTO            # status ACTIVE
def get_card(card_id: UUID) -> CardDTO                        # CardNotFound
def get_card_for_account(credit_account_id: UUID) -> CardDTO  # CardNotFound
def lock_card(card_id: UUID) -> CardDTO                       # FOR UPDATE
def ensure_can_authorize(card_id: UUID) -> CardDTO            # under lock; CardNotActive unless ACTIVE (BR-PUR-2)
def freeze_card(card_id: UUID) -> CardDTO                     # ACTIVE → INACTIVE
def unfreeze_card(card_id: UUID) -> CardDTO                   # INACTIVE → ACTIVE
def close_card(card_id: UUID) -> CardDTO                      # ACTIVE | INACTIVE → CLOSED
```

Exceptions: `CardsError`, `CardNotFound`, `CardNotActive`, `InvalidTransition`.

### Mandatory tests

TEST-SM-1, `last4` and token generation (format, uniqueness, the token never contains a 13–19 digit sequence), `CardDTO` has no `token` attribute, raw-SQL `last4` violations rejected, one card per account enforced by the database.

## 6. Orchestrator (`core`) in F1

- `core/money`: `parse_amount_minor(value) -> int` (strict: `int` only, rejects `bool`, `float`, `str`, `None`, `<= 0`; BR-MONEY-4/5) and `credit_limit_minor(deposit_minor, ratio_bps) -> int` (`deposit × ratio_bps // 10000`; BR-LIM-3), `COLLATERAL_RATIO_BPS = 10000` (BR-LIM-4).
- `core/management/commands/reconcile.py` (ledger.md §7): for every account, `outstanding_minor == receivable`; collateral balance matches the expected-balance table; `credit_limit_minor == credit_limit_minor(deposit, ratio)`; no unbalanced entries. Read-only; exit code 1 on any mismatch.
- Tests: TEST-REC-1/2, money parsing and limit (Hypothesis property: limit never exceeds `deposit × ratio / 10000`), no `FloatField`/`DecimalField` in any model.
