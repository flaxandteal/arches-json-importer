import logging

from celery import shared_task
from django.contrib.auth.models import User
from django.utils.translation import gettext as _

from arches.app.models import models
from arches.app.tasks import notify_completion

logger = logging.getLogger(__name__)


@shared_task
def load_arches_json(userid, files, summary, result, temp_dir, loadid, moduleid):
    from arches_json_importer.etl_modules import arches_json_importer

    try:
        importer = arches_json_importer.ArchesJsonImporter(
            loadid=loadid,
            temp_dir=temp_dir,
        )
        importer.userid = userid
        importer.moduleid = moduleid
        # Indexing is the long pole once the tiles are written, and it is
        # CPU-bound in Python (nested doc + term building), not in ES -- a
        # single process pegs one core of the box.  index_resources_by_transaction
        # fans out over ceil(cpu_count()/2) subprocesses when asked; max_subprocesses=0
        # means "use that default".
        importer.run_load_task(
            userid,
            files,
            summary,
            result,
            temp_dir,
            loadid,
            multiprocessing=True,
            max_subprocesses=0,
        )

        load_event = models.LoadEvent.objects.get(loadid=loadid)
        status = _("Completed") if load_event.status == "indexed" else _("Failed")
    except Exception as e:
        logger.error(e, exc_info=True)
        models.LoadEvent.objects.filter(loadid=loadid).update(status="failed")
        status = _("Failed")
    finally:
        msg = _("Arches JSON Import: {} [{}]").format(summary["name"], status)
        user = User.objects.get(id=userid)
        notify_completion(msg, user)
