from django.db import migrations
from django.db.models import F, Max


ARCHES_JSON_IMPORT_MODULE_PK = "0a1c9d67-8b4e-4f2a-9d31-6c5b2e7a4f18"

# Per-transaction rebuild of resource_x_resource. Core refreshes relationships a
# tile at a time, which dominates a 100k+ resource load. Plain RunSQL rather than
# a migrate_sql SQLItem: sql_config.py is core's registry and an application
# cannot add to it. CREATE OR REPLACE, so it stays safe if core later ships the
# same function.
REFRESH_RELATIONSHIPS_SQL = """CREATE OR REPLACE FUNCTION public.__arches_refresh_transaction_resource_relationships(
    transaction_id uuid
)
RETURNS boolean AS $$
BEGIN
    DELETE FROM resource_x_resource x
    USING edit_log el
    WHERE el.transactionid = transaction_id
      AND el.tileinstanceid IS NOT NULL
      AND x.tileid = el.tileinstanceid::uuid;

    WITH transaction_tileids AS (
        -- Dedupe on the small side: DISTINCT over the joined rows would sort on
        -- tiledata, which is ruinous once a load is millions of tiles.
        SELECT DISTINCT el.tileinstanceid::uuid AS tileid
        FROM edit_log el
        WHERE el.transactionid = transaction_id
          AND el.tileinstanceid IS NOT NULL
    ),
    transaction_tiles AS (
        SELECT t.tileid, t.resourceinstanceid, t.nodegroupid, t.tiledata
        FROM tiles t
        JOIN transaction_tileids tt ON tt.tileid = t.tileid
    ),
    relationships AS (
        SELECT t.tileid,
               t.resourceinstanceid,
               n.nodeid,
               jsonb_array_elements(t.tiledata->n.nodeid::text) AS relationship
        FROM transaction_tiles t
        JOIN nodes n ON n.nodegroupid = t.nodegroupid
        WHERE n.datatype IN ('resource-instance-list', 'resource-instance')
          AND jsonb_typeof(t.tiledata->n.nodeid::text) = 'array'
    )
    INSERT INTO resource_x_resource (
        resourcexid,
        notes,
        relationshiptype,
        resourceinstanceidfrom,
        resourceinstanceidto,
        inverserelationshiptype,
        tileid,
        nodeid,
        created,
        modified,
        resourceinstancefrom_graphid,
        resourceinstanceto_graphid
    )
    SELECT
        CASE COALESCE(r.relationship->>'resourceXresourceId', '')
            WHEN '' THEN uuid_generate_v4()
            ELSE (r.relationship->>'resourceXresourceId')::uuid
        END,
        '',
        r.relationship->>'ontologyProperty',
        r.resourceinstanceid,
        (r.relationship->>'resourceId')::uuid,
        r.relationship->>'inverseOntologyProperty',
        r.tileid,
        r.nodeid,
        now(),
        now(),
        resourcefrom.graphid,
        resourceto.graphid
    FROM relationships r
    LEFT JOIN resource_instances resourcefrom
           ON resourcefrom.resourceinstanceid = r.resourceinstanceid
    LEFT JOIN resource_instances resourceto
           ON resourceto.resourceinstanceid = (r.relationship->>'resourceId')::uuid
    WHERE r.relationship->>'resourceId' IS NOT NULL;

    RETURN true;
END;
$$ LANGUAGE plpgsql;
"""

DROP_REFRESH_RELATIONSHIPS_SQL = (
    "drop function if exists __arches_refresh_transaction_resource_relationships;"
)


def add_module(apps, schema_editor):
    ETLModule = apps.get_model("models", "ETLModule")
    max_helpsortorder = ETLModule.objects.aggregate(
        max_helpsortorder=Max(F("helpsortorder"))
    )["max_helpsortorder"]

    ETLModule.objects.update_or_create(
        pk=ARCHES_JSON_IMPORT_MODULE_PK,
        defaults={
            "name": "Import Arches JSON",
            "icon": "fa fa-upload",
            "etl_type": "import",
            "component": "views/components/etl_modules/arches-json-importer",
            "componentname": "arches-json-importer",
            "modulename": "arches_json_importer.py",
            "classname": "ArchesJsonImporter",
            "config": {
                "bgColor": "#4a6670",
                "circleColor": "#6c8a95",
                "show": True,
                "celeryByteSizeLimit": 100000,
                "logTileValues": False,
            },
            "reversible": True,
            "slug": "arches-json-importer",
            "description": "Import Arches JSON business data in bulk",
            "helptemplate": "arches-json-importer-help",
            "helpsortorder": (max_helpsortorder or 0) + 1,
        },
    )


def remove_module(apps, schema_editor):
    ETLModule = apps.get_model("models", "ETLModule")
    ETLModule.objects.filter(pk=ARCHES_JSON_IMPORT_MODULE_PK).delete()


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        # The last core migration that changes anything this one writes:
        # ETLModule.helptemplate and .helpsortorder. Deliberately not the
        # latest core migration -- depending on that would pin the application
        # to a single Arches patch release for no gain.
        ("models", "9558_add_plugin_help"),
    ]

    operations = [
        migrations.RunSQL(
            REFRESH_RELATIONSHIPS_SQL,
            DROP_REFRESH_RELATIONSHIPS_SQL,
        ),
        migrations.RunPython(add_module, remove_module),
    ]
