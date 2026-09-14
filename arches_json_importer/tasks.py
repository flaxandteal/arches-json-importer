import logging

from celery import shared_task
from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.translation import gettext as _

from arches.app.models import models
from arches.app.tasks import notify_completion

logger = logging.getLogger(__name__)


@shared_task
def load_arches_json(userid, files, summary, result, temp_dir, loadid, moduleid):
    from arches_json_importer.etl_modules import arches_json_importer

    # The finally clause below reports this, so it has to hold a value on every
    # path out -- including the ones no except clause here catches.
    status = _("Failed")
    try:
        importer = arches_json_importer.ArchesJsonImporter(
            loadid=loadid,
            temp_dir=temp_dir,
            userid=userid,
            moduleid=moduleid,
        )
        # No multiprocessing here. Indexing is the long pole and is CPU-bound in
        # Python, but this runs inside a celery prefork worker, whose processes
        # are daemonic - multiprocessing.Pool then raises "daemonic processes are
        # not allowed to have children", the tiles land and indexing never runs,
        # and the load ends as "unindexed". Fan-out belongs to the CLI, where
        # manage.py etl -mp -mxp N passes it through to run_load_task in an
        # ordinary process.
        importer.run_load_task(userid, files, summary, result, temp_dir, loadid)

        load_event = models.LoadEvent.objects.get(loadid=loadid)
        status = _("Completed") if load_event.status == "indexed" else _("Failed")
    except Exception as e:
        logger.error(e, exc_info=True)
        # Nothing else records this.  An exception raised outside validation --
        # a failed spatial-view refresh, say -- leaves load_errors empty, so
        # without error_message the error report reads "no errors found".
        models.LoadEvent.objects.filter(loadid=loadid).update(
            status="failed", error_message=str(e)
        )
        status = _("Failed")
    except BaseException:
        # revoke(terminate=True) raises outside the Exception hierarchy, so the
        # clause above never ran and the load_event sat on "running" forever --
        # which the etl-manager badge reads as "Validating".  Cancel it the way
        # the manager's own stop button does, then let the worker unwind.
        logger.info("Arches JSON Import cancelled: loadid=%s", loadid)
        models.LoadEvent.objects.filter(loadid=loadid).update(
            status="cancelled", load_end_time=timezone.now()
        )
        status = _("Cancelled")
        raise
    finally:
        msg = _("Arches JSON Import: {} [{}]").format(summary["name"], status)
        user = User.objects.get(id=userid)
        notify_completion(msg, user)
