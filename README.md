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

The import also runs headless through Arches' own `etl` command, which is how
large loads are usually done — the CLI path skips Celery (`celeryByteSizeLimit`
is a UI-only threshold) and runs in the foreground, so the load survives as long
as the shell does:

```sh
python manage.py etl arches-json-importer \
    -s coral/pkg/business_data/all_business_data.jsonl \
    -c /etl-config.json \
    -mp -mxp 15
```

| flag | meaning |
| --- | --- |
| *(positional)* | `arches-json-importer` — the module's `componentname`, or its uuid. Run `python manage.py etl` with no module to list what is registered. |
| `-s`, `--source` | Path to the `.json`, `.jsonl` or `.zip` to load. |
| `-c`, `--config` | Path to a JSON file of load options (below). Omit it for the defaults. |
| `-mp`, `--use_multiprocessing` | Index the loaded resources across subprocesses. |
| `-mxp`, `--max_subprocesses` | Pool size for `-mp`. Default `ceil(cpu_count()/2)`. |

The config file holds the same options the UI offers. Only one is read here:

```json
{ "overwrite": true }
```

`overwrite` deletes each incoming resource's existing instance before writing it
(cascading to its tiles), and relaxes the pre-load checks accordingly: a unique
constraint a resource violates against *itself* is not a clash, and cardinality
is not checked against tiles that are about to be replaced. Left off, a
resourceid that already exists is a validation failure.

The CLI has no request user, so the load is attributed to
`settings.DEFAULT_RESOURCE_IMPORT_USER`; it must exist or the command raises.

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
