import os
import uuid

from django.core.management.base import BaseCommand, CommandError
from django.utils.translation import gettext as _

from arches.app.models.models import ETLModule


class Command(BaseCommand):
    help = "Bulk import Arches JSON business data (.json, .jsonl, or a .zip of either)"

    def add_arguments(self, parser):
        parser.add_argument("source", help="The .json, .jsonl or .zip file to load")
        parser.add_argument(
            "-ow",
            "--overwrite",
            action="store_true",
            default=False,
            help="Replace resources that already exist instead of failing on them",
        )
        parser.add_argument(
            "--no-index",
            action="store_false",
            dest="index",
            default=True,
            help="Write the data but skip indexing; reindex later with es index_database",
        )
        parser.add_argument(
            "-mp",
            "--use_multiprocessing",
            action="store_true",
            default=False,
            help="Index across several processes after loading",
        )
        parser.add_argument(
            "-mxp",
            "--max_subprocesses",
            type=int,
            default=0,
            help="Processes to index with. Default is ceil(cpu_count()/2)",
        )

    def handle(self, *args, **options):
        from arches_json_importer.etl_modules.arches_json_importer import (
            ArchesJsonImporter,
        )

        source = options["source"]
        if not os.path.exists(source):
            raise CommandError(_("No such file: {}").format(source))

        try:
            module = ETLModule.objects.get(slug="arches-json-importer")
        except ETLModule.DoesNotExist:
            raise CommandError(
                _(
                    "The importer is not installed. Add it to INSTALLED_APPS and migrate."
                )
            )

        loadid = str(uuid.uuid4())
        # params reach the module through a synthesised request.POST, so every
        # value has to survive that round trip -- bools and ints both do.
        importer = ArchesJsonImporter(
            loadid=loadid,
            params={
                "module": str(module.etlmoduleid),
                "overwrite": options["overwrite"],
                "index": options["index"],
                "multiprocessing": options["use_multiprocessing"],
                "max_subprocesses": options["max_subprocesses"],
            },
        )

        self.stdout.write(_("Load {}").format(loadid))
        response = importer.cli(source)

        if response.get("success"):
            self.stdout.write(self.style.SUCCESS(_("Import finished.")))
            if not options["index"]:
                self.stdout.write(
                    _("Not indexed. Run: python manage.py es index_database")
                )
            return
        raise CommandError(response.get("data", {}).get("message", _("Import failed.")))
