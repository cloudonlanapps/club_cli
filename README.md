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

The `profile` block may carry the extended profile too (`clubDistrict`,
`preferredPositions`, `pastExperience`, `nationality`, `preferredLanguages`,
`guardian`); each preferred position or language must be one of the club's
options, so seed those first:

```bash
just profile_options seeds/profile_options.json
```

An application template (club_server#93) is seeded from its JSON, the body
`applications create` takes; `just application` skips one whose English title
a live template already has, and leaves the new one a draft:

```bash
just application seeds/applications/selection_trial.json
```

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
marketing, identity verification, the extended profile, applications) and `cli_test_modules_off.conf` has them
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

## Extended profile

Every extended field of a profile (club_server#92) has an option on
`user update` and `me update`, beside or instead of the JSON body; `--clear
<field>` sends one as null. `--membership-date` is an admin's alone.

```bash
club_client -u sudo --pw <pw> user update alice --club-district Pune \
  --preferred-position Forward --preferred-position Goalie --preferred-language Marathi \
  --guardian-name Ravi --guardian-relation Father --guardian-phone 9000000001
club_client -u sudo --pw <pw> user update alice --membership-date 2026-04-01
club_client -u alice --pw <pw> me update --nationality Indian --clear past-experience
club_client -u sudo --pw <pw> profile-options get
club_client -u sudo --pw <pw> profile-options set --file profile_options.json
```

The guardian options are merged into the stored guardian, so one part can
change alone. `--preferred-position` and `--preferred-language` repeat, and
are sent as the lists `preferredPositions` and `preferredLanguages`
(club_server#106); each value must be one of the club's options.
`profile-options set` replaces those options through the super admin's
`PATCH /v1/admin/preferences/profile_options`, the same write as
`preferences set profile_options` (club_server#98); it answers 503 while
`capabilities` reports `extendedProfile` false.
The fields are stored, and shown by `user get` / `user private`, either way.

## Applications

`applications` wraps the printable application forms (club_server#93). An
admin writes a template as JSON (`applications create --help` shows its
shape), moves it draft → saved → published, and previews it at any state; a
member who has uploaded an identity document lists the published ones and
downloads their own form. Every printed text is a language map, `{lang: text}`,
and may hold `{name}` gaps that the server fills at print from the template's
facts (club_server#126). `applications clauses` lists the built-in clauses and
the club's own texts together (`source` `builtin` with a `key`, or `club` with
an `id`), as written, gaps included, to copy into a template. The club's own
texts are kept with `applications library` (admin only).

```bash
club_client -u sudo --pw <pw> applications labels          # the label catalogue
club_client -u sudo --pw <pw> applications clauses         # built-in clauses and club texts, with their gaps
club_client -u sudo --pw <pw> applications library list
club_client -u sudo --pw <pw> applications library create --kind rule --name Fee \
  --heading en Fee --text en 'A fee of ₹{amount} is payable.' --text mr '₹{amount} शुल्क भरावे.'
club_client -u sudo --pw <pw> applications library update 4 --kind note --clear-heading
club_client -u sudo --pw <pw> applications library delete 4  # or get 4
club_client -u sudo --pw <pw> applications create --file trial.json
club_client -u sudo --pw <pw> applications update 3 --json '{"subtitle": null}'
club_client -u sudo --pw <pw> applications preview 3 -o preview.pdf
club_client -u sudo --pw <pw> applications save 3          # or unpublish / revert / duplicate / delete
club_client -u sudo --pw <pw> applications publish 3
club_client -u sudo --pw <pw> applications list --status published --limit 50
club_client -u sudo --pw <pw> applications form 3 --user alice
club_client -u alice --pw <pw> applications form 3         # writes application-3-alice.pdf
club_client -u alice --pw <pw> applications form 3 --category 1   # fits several age categories
```

The PDF commands write `-o FILE`, or by default `application-<id>-<username>.pdf`
in the current directory, and print the path. A member who fits more than one age
category picks one with `--category <index>` (`applications show` lists the
ones they fit as `fittingCategories`); otherwise the form is refused with
`APPLICATION_CATEGORY_REQUIRED` (club_server#108). `save` is refused with
`APPLICATION_INCOMPLETE` (each text is named with the print languages it lacks),
`APPLICATION_INVALID_REFERENCE`, `APPLICATION_UNKNOWN_FACT` (each place,
language and `{gap}` that names no fact of the template) or
`APPLICATION_DOES_NOT_FIT` (the overflow in points); each refusal gets a one-line hint on stderr. Applications need
`APPLICATIONS_ENABLED` and `IDENTITY_VERIFICATION_REQUIRED` both on; otherwise
`capabilities` reports `applications: false` and every command answers 503
`APPLICATIONS_DISABLED`.

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
