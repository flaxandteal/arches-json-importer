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

## Version coupling

Tested against Arches 8.2. **No `arches` pin is declared** — the host project
already pins it, and a specifier here can only fight that pin (see the comment
in `pyproject.toml`). Treat 8.2 as the supported minor. The module imports
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
