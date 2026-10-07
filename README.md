# club_cli

A command-line client for the [club_server](https://github.com/cloudonlanapps/club_server)
API. `club_client` wraps every endpoint the server offers; `club_admin` holds
the super-admin maintenance commands (hard deletes, upload cleanup).

```bash
uv run --project client club_client --base-url http://127.0.0.1:8400 -u sudo --pw <password> users list
uv run --project client club_client --help
```

## Seed

The `justfile` seeds a server from JSON files, one entity per recipe, through
the CLI. The server is `CLUB_URL`, the admin is `CLUB_USER` (default `sudo`)
and the admin's password is `CLUB_ADMIN_PW`, or the `pass` entry named by
`CLUB_PASS_ENTRY`; see `just env`. No seed data ships with this repo.

```bash
export CLUB_URL=https://api.myexampleclub.com CLUB_ADMIN_PW=<password>
just venue seeds/venue/main_hall.json
just group seeds/groups/group_u12.json
just coach seeds/users/coach_one.json
just member seeds/users/member_one.json
just event seeds/events/summer_camp.json
```

A user seed carries the registration fields plus three seed-only blocks that
`auth register` / `users register` strip and the `member` / `coach` recipes
apply afterwards: `profile` (via `me update`), `identity-document` (via
`me gallery add-file`) and `staffListing` (`{position, guest, hidden}` on the
public staff listing, via `staff set`; the `coach` recipes apply it).

Where the deployment does not verify identity (`capabilities` reports
`identityVerification: false`), a registered user is already `pending`: the
`member` recipe then skips the document upload and submit-for-review and goes
straight to approval, and `distribution_seed` folds its `registered` share
into `pending`.

## Test

```bash
just test                                   # fresh stacks, modules on and off
just test -k occurrence                     # pytest arguments pass through
CLUB_TEST_CONFS=cli_test.conf:on just test  # one stack only
just test_to http://127.0.0.1:8400         # a running server, read-only checks
```

`just test` needs `just`, `uv`, `jq`, `git` and PostgreSQL installed, and
nothing else. It runs the suite twice, each time against a fresh club_server
cloned at `main` by `background_server.sh` from
[native_deploy](https://github.com/cloudonlanapps/native_deploy):
`cli_test.conf` has every optional module on (credits, evaluations, event
marketing, identity verification) and `cli_test_modules_off.conf` has them
all off. Point a conf's `source` at a local checkout to test uncommitted
server changes.

`just test` clones native_deploy itself, into `.native_deploy/` (gitignored),
and brings it up to date on every run. `NATIVE_DEPLOY_REF` selects a branch
or a commit (default `main`); `NATIVE_DEPLOY_URL` selects another remote or a
local checkout.

```bash
NATIVE_DEPLOY_REF=<branch-or-commit> just test
```

- Most tests are offline: `tests/fakes.py` stands in for `httpx` and checks
  which endpoint each command calls, with what body.
- `test_endpoint_contract.py` reads the server's `openapi.json` and checks,
  method by method, that every operation the CLI calls exists and that every
  operation the server offers has a command. A new server endpoint fails it
  until it is wrapped. It checks one level down as well: every query
  parameter an operation takes, and every field of a request body a command
  builds itself, must be sent by a command, so a new server option fails it
  until a command has it. What is deliberately not sent is listed in
  `UNSENT_BY_DESIGN` with its reason.
- `test_live_*.py` drive the CLI against the server and assert whichever
  mode the stack reports. They create users, events and uploads, so they run
  only with `CLUB_LIVE_WRITES=1`, which `just test` sets and `just test_to`
  does not. A test that means something in one mode only is marked
  `modules_on` or `modules_off`, and the other run deselects it.

`just test` sets `CLUB_REQUIRE_SERVER=1`, so a stack that did not come up
fails the run instead of skipping the live tests into a green result.

## Encrypt existing media (at rest)

Authenticate as a super-admin. The server
must have `ENCRYPTION_KEY` deployed.

```bash
BASE=https://api.myexampleclub.com
AUTH="-u sudo --pw <password> --base-url $BASE"

# List still-plaintext identity documents (default tag: identity_document)
club_client $AUTH media unencrypted

# Preview the sweep without changing anything
club_client $AUTH media encrypt-existing --dry-run

# Encrypt every still-plaintext identity document (idempotent; safe to re-run)
club_client $AUTH media encrypt-existing

# Encrypt a single media by UUID
club_client $AUTH media encrypt <media-uuid>

# Widen beyond identity documents: pass an empty tag to sweep every tag
club_client $AUTH media encrypt-existing --tag ''
```
