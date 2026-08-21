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
        importer.run_load_task(userid, files, summary, result, temp_dir, loadid)

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
