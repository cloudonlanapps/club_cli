# Task runner for the club_server CLI: the test suite, and recipes that seed a
# server from JSON files through the CLI.
#
#   CLUB_URL         the server to talk to (default: a local one on 8400)
#   CLUB_USER        the admin who runs the admin steps (default: sudo)
#   CLUB_ADMIN_PW    that admin's password, or
#   CLUB_PASS_ENTRY  the `pass` entry that holds it
#   CLUB_SEEDS       the seed directory the *_all recipes read (default: seeds)
URL        := env_var_or_default("CLUB_URL", "http://127.0.0.1:8400")
USER       := env_var_or_default("CLUB_USER", "sudo")
PASS_ENTRY := env_var_or_default("CLUB_PASS_ENTRY", "")
SEEDS      := env_var_or_default("CLUB_SEEDS", "seeds")

# The client's test suite runs against fresh isolated club_server stacks,
# started by native_deploy's background_server.sh from the confs in this repo,
# which clone club_server at main. `just test` clones native_deploy itself,
# into .native_deploy/ (gitignored); NATIVE_DEPLOY_REF picks a branch or a
# commit, NATIVE_DEPLOY_URL another remote or a local checkout. Each entry is
# <conf>:<mode>, mode being what the stack's optional modules are expected to
# be: the suite runs once with every module on and once with every module off.
TEST_CONFS := env_var_or_default("CLUB_TEST_CONFS", "cli_test.conf:on cli_test_modules_off.conf:off")

NATIVE_DEPLOY_URL := env_var_or_default("NATIVE_DEPLOY_URL", "https://github.com/cloudonlanapps/native_deploy.git")
NATIVE_DEPLOY_REF := env_var_or_default("NATIVE_DEPLOY_REF", "main")
NATIVE_DEPLOY_DIR := justfile_directory() / ".native_deploy"
BACKGROUND_SERVER := NATIVE_DEPLOY_DIR / "background_server.sh"

default:
    @just --list

# ── Tests ───────────────────────────────────────────────────────────────

#   just test                          # both stacks: modules on, modules off
#   just test -k media                 # pytest arguments pass through
#   CLUB_TEST_CONFS=cli_test.conf:on just test    # one stack only
# Run client/tests against fresh isolated servers, one per module mode.
[positional-arguments]
test *ARGS: _native-deploy
    #!/usr/bin/env bash
    set -euo pipefail
    run_one() {  # <conf> <on|off> [pytest args...]
        local conf="$1" mode="$2" json base port db_port
        shift 2
        json=$("{{BACKGROUND_SERVER}}" "$conf" start --auto-ports)
        base=$(printf '%s' "$json" | jq -r .host_url)
        port=$(printf '%s' "$json" | jq -r .server_port)
        db_port=$(printf '%s' "$json" | jq -r .db_port)
        trap '"{{BACKGROUND_SERVER}}" "'"$conf"'" cleanup port='"$port"' db_port='"$db_port" EXIT
        echo "==> client tests against $base ($conf, optional modules $mode)"
        # REQUIRE_SERVER: a stack that is not answering fails the run rather
        # than skipping every live test into a green result. LIVE_WRITES: this
        # stack is throwaway, so the live tests may create what they need.
        CLUB_API_BASE_URL="$base" CLUB_USERNAME=sudo CLUB_PASSWORD=devboot \
        CLUB_REQUIRE_SERVER=1 CLUB_LIVE_WRITES=1 CLUB_EXPECT_MODULES="$mode" \
            uv run --frozen --project client --extra dev pytest client/tests "$@"
    }
    failed=()
    for entry in {{TEST_CONFS}}; do
        ( run_one "${entry%%:*}" "${entry##*:}" "$@" ) || failed+=("$entry")
    done
    if (( ${#failed[@]} )); then
        echo "==> FAILED against: ${failed[*]}" >&2
        exit 1
    fi
    echo "==> passed against: {{TEST_CONFS}}"

# Internal: clone native_deploy into .native_deploy/ on first use, then bring
# it to NATIVE_DEPLOY_REF (a branch is taken at its remote head, so every run
# pulls).
_native-deploy:
    #!/usr/bin/env bash
    set -euo pipefail
    dir="{{NATIVE_DEPLOY_DIR}}"
    if [ ! -d "$dir/.git" ]; then
        # Clone beside the target and rename, so concurrent runs never see a
        # half-written clone; the loser of the rename drops its copy.
        tmp=$(mktemp -d "$dir.XXXXXX")
        trap 'rm -rf "$tmp"' EXIT
        echo "==> cloning {{NATIVE_DEPLOY_URL}} into $dir" >&2
        git clone --quiet "{{NATIVE_DEPLOY_URL}}" "$tmp/clone" >&2
        mv -T "$tmp/clone" "$dir" 2>/dev/null || [ -d "$dir/.git" ]
    fi
    git -C "$dir" remote set-url origin "{{NATIVE_DEPLOY_URL}}"
    git -C "$dir" fetch --quiet --prune origin >&2
    want=$(git -C "$dir" rev-parse --verify --quiet "origin/{{NATIVE_DEPLOY_REF}}^{commit}") \
        || want=$(git -C "$dir" rev-parse --verify "{{NATIVE_DEPLOY_REF}}^{commit}")
    if [ "$(git -C "$dir" rev-parse HEAD)" != "$want" ]; then
        git -C "$dir" checkout --quiet --detach "$want" >&2
        echo "==> native_deploy at $(git -C "$dir" rev-parse --short HEAD) ({{NATIVE_DEPLOY_REF}})" >&2
    fi
    [ -x "{{BACKGROUND_SERVER}}" ] || { echo "ERROR: {{BACKGROUND_SERVER}} is missing." >&2; exit 1; }

#   just test_to http://127.0.0.1:8400     # an already running server
# Run client/tests against a running server. Only the
# read-only live checks run: the rest write data and belong on a throwaway
# stack (set CLUB_LIVE_WRITES=1 to run them anyway).
[positional-arguments]
test_to URL *ARGS:
    #!/usr/bin/env bash
    set -euo pipefail
    PW=$(just _admin_pw)
    URL="$1"
    shift
    CLUB_API_BASE_URL="$URL" CLUB_USERNAME={{USER}} CLUB_PASSWORD="$PW" CLUB_REQUIRE_SERVER=1 \
        uv run --frozen --project client --extra dev pytest client/tests "$@"

env:
    @echo "CLUB_URL        = {{URL}}"
    @echo "CLUB_USER       = {{USER}}"
    @echo "CLUB_PASS_ENTRY = {{PASS_ENTRY}}"
    @echo "CLUB_SEEDS      = {{SEEDS}}"

# The admin password: CLUB_ADMIN_PW, or else the `pass` entry CLUB_PASS_ENTRY.
_admin_pw:
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ -n "${CLUB_ADMIN_PW:-}" ]]; then printf '%s\n' "$CLUB_ADMIN_PW"; exit 0; fi
    if [[ -z "{{PASS_ENTRY}}" ]]; then
        echo "set CLUB_ADMIN_PW, or CLUB_PASS_ENTRY to the pass entry that holds it" >&2; exit 1
    fi
    pass show "{{PASS_ENTRY}}" | head -n1

# ── Bulk ────────────────────────────────────────────────────────────────

# DELAY = seconds between server calls; use 0 on a local server, a non-zero
# value where requests are throttled. Reads the seed users in {{SEEDS}}/users.
# States: registered 20 / pending 20 / active 40 / blocked 10 / left 5 / del 5.
# Seed ~100 demo users across lifecycle states, with synthetic ID documents.
distribution_seed DELAY="0":
    @PW=$(just _admin_pw) && \
      ./scripts/seed_distribution \
        --base-url {{URL}} --src-dir {{SEEDS}}/users \
        --admin-user {{USER}} --admin-pw "$PW" --delay {{DELAY}}

# ── Composites ──────────────────────────────────────────────────────────

member JSON:
    #!/usr/bin/env bash
    set -euo pipefail
    USERNAME=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['username'])" {{JSON}})
    ADMIN_PW=$(just _admin_pw)
    if uv run --project client club_client --base-url {{URL}} -u {{USER}} --pw "$ADMIN_PW" \
         user get "$USERNAME" >/dev/null 2>&1; then
      echo "member exists, skipping: $USERNAME"; exit 0
    fi
    just register {{JSON}}
    # Where the deployment does not verify identity (club_server#428) the new
    # user is already pending: no document to upload, nothing to submit.
    STATUS=$(uv run --project client club_client --base-url {{URL}} -u {{USER}} --pw "$ADMIN_PW" \
               user get "$USERNAME" | python3 -c "import json,sys;print(json.load(sys.stdin)['status'])")
    if [[ "$STATUS" == "registered" ]]; then
      just me_identity_upload {{JSON}}
      just me_submit {{JSON}}
    else
      echo "already $STATUS (identity verification off), skipping document and submit: $USERNAME"
    fi
    just admin_approve {{JSON}}
    just me_update {{JSON}}


coach JSON:
    just member {{JSON}}
    just admin_add_role {{JSON}} coach
    just staff_set {{JSON}}

admin JSON:
    just member {{JSON}}
    just admin_add_role {{JSON}} admin

admin_coach JSON:
    just member {{JSON}}
    just admin_add_role {{JSON}} admin
    just admin_add_role {{JSON}} coach
    just staff_set {{JSON}}

# A guest coach is admin-created in one call (no onboarding), then curated.
guest JSON:
    #!/usr/bin/env bash
    set -euo pipefail
    USERNAME=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['username'])" {{JSON}})
    ADMIN_PW=$(just _admin_pw)
    if ! uv run --project client club_client --base-url {{URL}} -u {{USER}} --pw "$ADMIN_PW" \
         user get "$USERNAME" >/dev/null 2>&1; then
      uv run --project client club_client --base-url {{URL}} -u {{USER}} --pw "$ADMIN_PW" \
        users create {{JSON}} --guest
    else
      echo "guest exists, skipping create: $USERNAME"
    fi
    just staff_set {{JSON}}

# ── Resource creates (admin) ────────────────────────────────────────────

venue JSON:
    #!/usr/bin/env bash
    set -euo pipefail
    NAME=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['name'])" {{JSON}})
    PW=$(just _admin_pw)
    if uv run --project client club_client --base-url {{URL}} -u {{USER}} --pw "$PW" \
         venues list --limit 100 \
       | python3 -c "import json,sys; sys.exit(0 if any(v.get('name')==sys.argv[1] for v in json.load(sys.stdin).get('items',[])) else 1)" "$NAME"; then
      echo "venue exists, skipping: $NAME"; exit 0
    fi
    uv run --project client club_client --base-url {{URL}} -u {{USER}} --pw "$PW" \
      venues create {{JSON}}


group JSON:
    #!/usr/bin/env bash
    set -euo pipefail
    NAME=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['name'])" {{JSON}})
    PW=$(just _admin_pw)
    if uv run --project client club_client --base-url {{URL}} -u {{USER}} --pw "$PW" \
         groups list --limit 100 \
       | python3 -c "import json,sys; sys.exit(0 if any(g.get('name')==sys.argv[1] for g in json.load(sys.stdin).get('items',[])) else 1)" "$NAME"; then
      echo "group exists, skipping: $NAME"; exit 0
    fi
    uv run --project client club_client --base-url {{URL}} -u {{USER}} --pw "$PW" \
      groups create {{JSON}}


# Resolves venueName → venueId from the live `venues list` so events
# bind to whichever id the venue currently has.
event JSON:
    #!/usr/bin/env bash
    set -euo pipefail
    PW=$(just _admin_pw)
    TITLE=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['title'])" {{JSON}})
    if uv run --project client club_client --base-url {{URL}} -u {{USER}} --pw "$PW" \
         events list --include-past --limit 100 \
       | python3 -c "import json,sys; sys.exit(0 if any(e.get('title')==sys.argv[1] for e in json.load(sys.stdin).get('items',[])) else 1)" "$TITLE"; then
      echo "event exists, skipping: $TITLE"; exit 0
    fi
    PAYLOAD=$(python3 -c "
    import json,sys,subprocess
    d = json.load(open(sys.argv[1]))
    name = d.pop('venueName', None)
    if name:
        out = subprocess.check_output([
            'uv','run','--project','client','club_client',
            '--base-url','{{URL}}','-u','{{USER}}','--pw',sys.argv[2],
            'venues','list','--limit','100',
        ])
        items = json.loads(out).get('items', [])
        match = [v for v in items if v.get('name')==name]
        if not match:
            print(f'venue not found: {name}', file=sys.stderr); sys.exit(2)
        d['venueId'] = match[0]['id']
    print(json.dumps(d))
    " {{JSON}} "$PW")
    echo "$PAYLOAD" \
      | uv run --project client club_client --base-url {{URL}} -u {{USER}} --pw "$PW" \
          events create -

# ── Card generation (offline) ───────────────────────────────────────────

gen_card JSON:
    ./scripts/seed_gen_card {{JSON}}

# Generate QR code PNG for a URL. Extra args: -o OUT -t LINE -l LOGO -s SIZE
gen_qr URL *ARGS:
    ./scripts/qr_generator {{URL}} {{ARGS}}

# ── Atomic ──────────────────────────────────────────────────────────────

register JSON:
    @uv run --project client club_client --base-url {{URL}} auth register {{JSON}}

# Forces tag=identity_document regardless of what the JSON tag was
# (submit-for-review gates on this exact string).
me_identity_upload JSON:
    #!/usr/bin/env bash
    set -euo pipefail
    USERNAME=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['username'])" {{JSON}})
    PASSWORD=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['password'])" {{JSON}})
    JSON_DIR=$(dirname {{JSON}})
    python3 -c "import json,sys;[print(p) for p in json.load(open(sys.argv[1])).get('identity-document',[])]" {{JSON}} \
      | while IFS= read -r REL; do
          uv run --project client club_client --base-url {{URL}} -u "$USERNAME" --pw "$PASSWORD" \
            me gallery add-file "$JSON_DIR/$REL" --tag identity_document
        done

me_submit JSON:
    #!/usr/bin/env bash
    set -euo pipefail
    USERNAME=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['username'])" {{JSON}})
    PASSWORD=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['password'])" {{JSON}})
    uv run --project client club_client --base-url {{URL}} -u "$USERNAME" --pw "$PASSWORD" \
      me submit-for-review

# No-op if JSON has no `profile` block.
me_update JSON:
    #!/usr/bin/env bash
    set -euo pipefail
    PROFILE=$(python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print(json.dumps(d.get('profile',{})))" {{JSON}})
    if [[ "$PROFILE" == "{}" ]]; then exit 0; fi
    USERNAME=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['username'])" {{JSON}})
    PASSWORD=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['password'])" {{JSON}})
    uv run --project client club_client --base-url {{URL}} -u "$USERNAME" --pw "$PASSWORD" \
      me update "$PROFILE"

admin_approve JSON:
    #!/usr/bin/env bash
    set -euo pipefail
    USERNAME=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['username'])" {{JSON}})
    ADMIN_PW=$(just _admin_pw)
    uv run --project client club_client --base-url {{URL}} -u {{USER}} --pw "$ADMIN_PW" \
      user approve "$USERNAME"

# Skips a role the user already holds, so the composites can be re-run.
admin_add_role JSON ROLE:
    #!/usr/bin/env bash
    set -euo pipefail
    USERNAME=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['username'])" {{JSON}})
    ADMIN_PW=$(just _admin_pw)
    if uv run --project client club_client --base-url {{URL}} -u {{USER}} --pw "$ADMIN_PW" \
         user get "$USERNAME" \
       | python3 -c "import json,sys; sys.exit(0 if sys.argv[1] in json.load(sys.stdin).get('roles',[]) else 1)" {{ROLE}}; then
      echo "role exists, skipping: $USERNAME {{ROLE}}"; exit 0
    fi
    uv run --project client club_client --base-url {{URL}} -u {{USER}} --pw "$ADMIN_PW" \
      user add-role "$USERNAME" {{ROLE}}

# Applies the seed's `staffListing` block ({position, guest, hidden}) to the
# public staff listing. No-op if the JSON has none.
staff_set JSON:
    #!/usr/bin/env bash
    set -euo pipefail
    ARGS=$(python3 -c "
    import json,sys
    s = json.load(open(sys.argv[1])).get('staffListing') or {}
    out = []
    if 'position' in s: out += ['--position', str(s['position'])]
    if 'guest' in s: out.append('--guest' if s['guest'] else '--no-guest')
    if 'hidden' in s: out.append('--hidden' if s['hidden'] else '--no-hidden')
    print(' '.join(out))
    " {{JSON}})
    if [[ -z "$ARGS" ]]; then exit 0; fi
    USERNAME=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['username'])" {{JSON}})
    ADMIN_PW=$(just _admin_pw)
    uv run --project client club_client --base-url {{URL}} -u {{USER}} --pw "$ADMIN_PW" \
      staff set "$USERNAME" $ARGS

admin_remove_role JSON ROLE:
    #!/usr/bin/env bash
    set -euo pipefail
    USERNAME=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['username'])" {{JSON}})
    ADMIN_PW=$(just _admin_pw)
    uv run --project client club_client --base-url {{URL}} -u {{USER}} --pw "$ADMIN_PW" \
      user remove-role "$USERNAME" {{ROLE}}

# ── Deletion (atomic soft-deletes; one-off debug use) ───────────────────

# `user delete` is a soft delete (server-side). Skips silently if missing.
user_delete JSON:
    #!/usr/bin/env bash
    set -euo pipefail
    USERNAME=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['username'])" {{JSON}})
    ADMIN_PW=$(just _admin_pw)
    if ! uv run --project client club_client --base-url {{URL}} -u {{USER}} --pw "$ADMIN_PW" \
           user get "$USERNAME" >/dev/null 2>&1; then
      echo "user not found: $USERNAME"; exit 0
    fi
    uv run --project client club_client --base-url {{URL}} -u {{USER}} --pw "$ADMIN_PW" \
      user delete "$USERNAME"

venue_delete JSON:
    #!/usr/bin/env bash
    set -euo pipefail
    NAME=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['name'])" {{JSON}})
    ADMIN_PW=$(just _admin_pw)
    ID=$(uv run --project client club_client --base-url {{URL}} -u {{USER}} --pw "$ADMIN_PW" \
           venues list --limit 100 \
         | python3 -c "import json,sys; [print(v['id']) for v in json.load(sys.stdin).get('items',[]) if v.get('name')==sys.argv[1]]" "$NAME")
    if [[ -z "$ID" ]]; then echo "venue not found: $NAME"; exit 0; fi
    uv run --project client club_client --base-url {{URL}} -u {{USER}} --pw "$ADMIN_PW" \
      venue delete "$ID"

group_delete JSON:
    #!/usr/bin/env bash
    set -euo pipefail
    NAME=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['name'])" {{JSON}})
    ADMIN_PW=$(just _admin_pw)
    ID=$(uv run --project client club_client --base-url {{URL}} -u {{USER}} --pw "$ADMIN_PW" \
           groups list --limit 100 \
         | python3 -c "import json,sys; [print(g['id']) for g in json.load(sys.stdin).get('items',[]) if g.get('name')==sys.argv[1]]" "$NAME")
    if [[ -z "$ID" ]]; then echo "group not found: $NAME"; exit 0; fi
    uv run --project client club_client --base-url {{URL}} -u {{USER}} --pw "$ADMIN_PW" \
      group delete "$ID"

event_delete JSON:
    #!/usr/bin/env bash
    set -euo pipefail
    TITLE=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['title'])" {{JSON}})
    ADMIN_PW=$(just _admin_pw)
    ID=$(uv run --project client club_client --base-url {{URL}} -u {{USER}} --pw "$ADMIN_PW" \
           events list --include-past --limit 100 \
         | python3 -c "import json,sys; [print(e['id']) for e in json.load(sys.stdin).get('items',[]) if e.get('title')==sys.argv[1]]" "$TITLE")
    if [[ -z "$ID" ]]; then echo "event not found: $TITLE"; exit 0; fi
    uv run --project client club_client --base-url {{URL}} -u {{USER}} --pw "$ADMIN_PW" \
      event delete "$ID"

# ── Hard deletion (soft → hard; reseed-compatible) ──────────────────────

users_hard_delete_all:
    #!/usr/bin/env bash
    set -euo pipefail
    for f in {{SEEDS}}/users/*.json; do just user_hard_delete "$f"; done


venues_hard_delete_all:
    #!/usr/bin/env bash
    set -euo pipefail
    for f in {{SEEDS}}/venue/*.json; do just venue_hard_delete "$f"; done


groups_hard_delete_all:
    #!/usr/bin/env bash
    set -euo pipefail
    for f in {{SEEDS}}/groups/*.json; do just group_hard_delete "$f"; done


events_hard_delete_all:
    #!/usr/bin/env bash
    set -euo pipefail
    for f in {{SEEDS}}/events/*.json; do just event_hard_delete "$f"; done


# Wipes everything under the seed directory (hard-delete + orphan cleanup).
# Order: events → groups → venues → users → uploads orphan cleanup (FK-safe).
delete_all:
    just events_hard_delete_all
    just groups_hard_delete_all
    just venues_hard_delete_all
    just users_hard_delete_all
    just orphan_uploads_cleanup

orphan_uploads_cleanup:
    @PW=$(just _admin_pw) && \
      uv run --project client club_admin \
        --base-url {{URL}} -u {{USER}} --pw "$PW" \
        uploads cleanup

# Soft-deletes if active, then hard-deletes. No-op if user is gone.
user_hard_delete JSON:
    #!/usr/bin/env bash
    set -euo pipefail
    USERNAME=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['username'])" {{JSON}})
    ADMIN_PW=$(just _admin_pw)
    if ! REC=$(uv run --project client club_client --base-url {{URL}} -u {{USER}} --pw "$ADMIN_PW" \
                 user get "$USERNAME" 2>/dev/null); then
      echo "user not found: $USERNAME"; exit 0
    fi
    DELETED=$(echo "$REC" | python3 -c "import json,sys; print('1' if json.load(sys.stdin).get('deletedAtUtc') else '0')")
    if [[ "$DELETED" == "0" ]]; then
      uv run --project client club_client --base-url {{URL}} -u {{USER}} --pw "$ADMIN_PW" \
        user delete "$USERNAME" > /dev/null
    fi
    uv run --project client club_admin --base-url {{URL}} -u {{USER}} --pw "$ADMIN_PW" \
      user hard-delete "$USERNAME"

venue_hard_delete JSON:
    #!/usr/bin/env bash
    set -euo pipefail
    NAME=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['name'])" {{JSON}})
    ADMIN_PW=$(just _admin_pw)
    ID=$(uv run --project client club_client --base-url {{URL}} -u {{USER}} --pw "$ADMIN_PW" \
           venues list --limit 100 \
         | python3 -c "import json,sys; [print(v['id']) for v in json.load(sys.stdin).get('items',[]) if v.get('name')==sys.argv[1]]" "$NAME")
    if [[ -z "$ID" ]]; then echo "venue not found: $NAME"; exit 0; fi
    uv run --project client club_client --base-url {{URL}} -u {{USER}} --pw "$ADMIN_PW" \
      venue delete "$ID" > /dev/null
    uv run --project client club_admin --base-url {{URL}} -u {{USER}} --pw "$ADMIN_PW" \
      venue hard-delete "$ID"

group_hard_delete JSON:
    #!/usr/bin/env bash
    set -euo pipefail
    NAME=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['name'])" {{JSON}})
    ADMIN_PW=$(just _admin_pw)
    ID=$(uv run --project client club_client --base-url {{URL}} -u {{USER}} --pw "$ADMIN_PW" \
           groups list --limit 100 \
         | python3 -c "import json,sys; [print(g['id']) for g in json.load(sys.stdin).get('items',[]) if g.get('name')==sys.argv[1]]" "$NAME")
    if [[ -z "$ID" ]]; then echo "group not found: $NAME"; exit 0; fi
    uv run --project client club_client --base-url {{URL}} -u {{USER}} --pw "$ADMIN_PW" \
      group delete "$ID" > /dev/null
    uv run --project client club_admin --base-url {{URL}} -u {{USER}} --pw "$ADMIN_PW" \
      group hard-delete "$ID"

event_hard_delete JSON:
    #!/usr/bin/env bash
    set -euo pipefail
    TITLE=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['title'])" {{JSON}})
    ADMIN_PW=$(just _admin_pw)
    ID=$(uv run --project client club_client --base-url {{URL}} -u {{USER}} --pw "$ADMIN_PW" \
           events list --include-past --limit 100 \
         | python3 -c "import json,sys; [print(e['id']) for e in json.load(sys.stdin).get('items',[]) if e.get('title')==sys.argv[1]]" "$TITLE")
    if [[ -z "$ID" ]]; then echo "event not found: $TITLE"; exit 0; fi
    uv run --project client club_client --base-url {{URL}} -u {{USER}} --pw "$ADMIN_PW" \
      event delete "$ID" > /dev/null
    uv run --project client club_admin --base-url {{URL}} -u {{USER}} --pw "$ADMIN_PW" \
      event hard-delete "$ID"
