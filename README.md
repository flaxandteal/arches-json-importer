# arches-json-importer

Bulk import of Arches JSON business data through the ETL loader, packaged as an
Arches application. Built for large loads (100k+ resources).

Two design choices distinguish it from the other import modules, both driven by
that scale:

1. **Passing tiles never enter `load_staging`.** The module writes tiles itself,
   so the staging buffer would be a second full copy of the data in Postgres for
   no benefit. Rows that *fail* validation are still staged, so the existing
   error-report UI works unchanged.
2. **Tile values are validated but never transformed.** Arches JSON is an
   export: its values are already in tile shape. Running them through
   `transform_value_for_tile` would mint fresh `file_id`s and orphan the
   existing files.

Input formats: `.json` (an Arches business-data export), `.jsonl` (one resource
object per line), or a `.zip` of either. **Prefer .jsonl for very large loads** —
it streams, whereas a `.json` file is parsed whole:

```sh
jq -c '.business_data.resources[]' export.json > export.jsonl
```

## Install

```sh
pip install arches-json-importer
```

Then in your project's `settings.py`:

```python
INSTALLED_APPS = (
    ...
    "arches_json_importer",
)
```

and run:

```sh
python manage.py migrate
npm run build_development   # picks up the module's JS and templates
```

The migration creates the `__arches_refresh_transaction_resource_relationships`
Postgres function and registers the ETL module, which then appears under
*Bulk Data Manager → Import*.

## Command line

Large loads are usually run headless. The command runs in the foreground and
skips Celery entirely (`celeryByteSizeLimit` is a UI-only threshold), so the
load lasts as long as the shell does.

```sh
python manage.py import_arches_json coral/pkg/business_data/all_business_data.jsonl -ow -mp -mxp 15
```

| flag | default | meaning |
| --- | --- | --- |
| *(positional)* | — | The `.json`, `.jsonl` or `.zip` to load. |
| `-ow`, `--overwrite` | off | Replace resources whose ids already exist. |
| `--no-index` | indexing on | Write the tiles but skip indexing. |
| `-mp`, `--use_multiprocessing` | off | Index across subprocesses after loading. |
| `-mxp`, `--max_subprocesses` | `ceil(cpu_count()/2)` | Pool size for `-mp`. |

`overwrite` deletes each incoming resource's existing instance before writing it
(cascading to its tiles), and relaxes the pre-load checks accordingly: a unique
constraint a resource violates against *itself* is not a clash, and cardinality
is not checked against tiles that are about to be replaced. Left off, a
resourceid that already exists is a validation failure.

`--no-index` finishes the load with status `unindexed`: the tiles are durable
but will not appear in search until you run `python manage.py index_database`.
Useful when loading several files in a row — skip indexing on each and index
once at the end:

```sh
for f in part-*.jsonl; do
    python manage.py import_arches_json "$f" --no-index
done
python manage.py index_database
```

Indexing runs **once per load, not per resource**: tiles are written with the
tile triggers disabled, and the Elasticsearch documents are built in a single
pass at the end, scoped by the load's transaction id. So `--no-index` is
all-or-nothing, and a failure there leaves the whole load unindexed rather than
a fraction of it.

The CLI has no request user, so the load is attributed to
`settings.DEFAULT_RESOURCE_IMPORT_USER`; it must exist or the command raises.

The generic `python manage.py etl arches-json-importer -s <file> -c <config.json>`
still works, but needs a JSON config file for the same options and is not
recommended.

### In the ETL manager

The panel offers **Overwrite resources that already exist** and **Index the data
after import**, matching the two flags above. Multiprocessing is deliberately
not offered: a load over `celeryByteSizeLimit` is handed to a Celery worker
whose prefork processes are daemonic, so a pool there raises "daemonic processes
are not allowed to have children" and the load would finish written but never
indexed. Use the command line when you want indexing fan-out.

### Reversal

The module is registered `reversible: False`. With overwrite on it deletes and
re-creates resources, logging `create`/`tile create` edits — and
`reverse_edit_log_entries` *deletes* on those, so a reverse would destroy the
originals rather than restore them. Core's JSON-LD importer is registered the
same way for the same reason.

## Version coupling

Pinned to `arches>=8.2.0a1,<8.3`. Both halves matter and the comment in
`pyproject.toml` says why: Arches' own system check requires every application
to declare an `arches` requirement, and the alpha in the lower bound is what
lets `8.2.0a8` satisfy it. The module imports
`_post_save_edit_log`, `disable_tile_triggers` and `reenable_tile_triggers` from
`arches.app.etl_modules.save` — the first is private. That needs rechecking
against any new Arches minor. The migration deliberately depends on the old
`models.9558_add_plugin_help` (the last core migration touching the ETLModule
columns it writes) rather than on whatever core migration is newest, so
installing does not pin a patch release.

## Tests

```sh
python manage.py test tests.arches_json_import_tests --settings="tests.test_settings"
```

Run from a project that has this application installed; the tests are
`SimpleTestCase` and touch neither the database nor a load event.
