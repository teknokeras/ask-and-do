# Larkspur demo docs

The test corpus for ask-and-do: 28 pages of docs for **Larkspur**, a fictional workspace admin product. We write them ourselves so the ground truth for the evals is known. They describe the same API that the mock admin API in `prepare/demo/admin_api/` implements.

```sh
just docs-serve                          # build and serve on http://localhost:8001
just ingest --url http://localhost:8001/ # in another terminal, with the database up
```

`just docs-build` only builds. Output goes to `build/` (gitignored).

## How it is built

`content/` holds one HTML fragment per page, with a metadata comment at the top (title, section, order, description). `build.py` (standard library only) wraps each fragment in the same layout a real docs site has: header, sidebar, breadcrumbs, an "On this page" list, prev/next links and a footer. The ingest has to strip all of that. The build also:

- gives every `<h2>`/`<h3>` an id and a `#` self-link, as doc generators do,
- writes `sitemap.xml` and `robots.txt`,
- fails on internal links to missing pages or sections.

## What the docs cover on purpose

These are the cases from `docs/PLAN.md` section 7. The evals rely on them:

| Case | Where |
|---|---|
| Near-duplicate pair: create a user (`POST /v1/users`, active now) vs invite (`POST /v1/invitations`, pending for 7 days) | `users/creating-users`, `users/inviting-users` |
| Replace vs partial update: `PUT /v1/groups/{id}` clears omitted fields, including members; `PATCH` changes only what is sent | `groups/updating-groups` |
| Cryptic operation: `POST /v1/users/{id}/xfer` moves all of a user's projects | `projects/transferring-ownership` |
| Same name, different effect: remove a member from a group vs from the organization (`DELETE /v1/org/members/{id}`) | `groups/group-members`, `users/removing-users` |
| Destructive endpoints (should stay off the allowlist): delete a project, delete a group, remove from the organization | `projects/deleting-projects`, `groups/deleting-groups`, `users/removing-users` |
| Facts that are easy to get wrong: role changes apply to new tokens only, viewers get 404 (not 403) for hidden projects, project keys can't be changed | `users/changing-roles`, `roles/permissions`, `projects/creating-projects` |

## What the docs do not cover on purpose

Questions about these should get "not found". They are good near-miss and unanswerable eval cases: SSO or SAML, billing and pricing, audit logs, webhooks, two-factor authentication, SDKs or client libraries, renaming the organization, data export.

A few questions have a documented "no" (custom roles, restoring a deleted project, changing a project key, changing a user's email, raising rate limits). For those, the right answer is the documented "no", not "not found".

## Changing the docs

Edit a fragment in `content/`, and update the admin API if the change describes different behaviour. Page count and some section anchors are asserted in `tests/` and in `prepare/ingest/tests/test_demo_site.py`. Facts that eval cases depend on should change together with those cases.
