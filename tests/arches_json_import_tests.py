from django.test import SimpleTestCase

from arches_json_importer.etl_modules.arches_json_importer import (
    ArchesJsonImporter,
    DB_CHECK_BATCH_SIZE,
    MEMO_MAX_VALUES_PER_NODE,
)

# these tests can be run from the command line via
# python manage.py test tests.arches_json_import_tests --settings="tests.test_settings"


def _bare_importer():
    """An instance without __init__, which would need a user and a load event.

    These tests cover the pure validation logic, which touches neither.
    """
    importer = object.__new__(ArchesJsonImporter)
    importer.validated_data = {}
    importer._memo_disabled_nodes = set()
    return importer


class FileListShapeTests(SimpleTestCase):
    """Arches 8 stores file-list metadata as {lang: {value, direction}}. A bare
    string poisons the Elasticsearch mapping for every later document, so it has
    to be rejected at import rather than written through."""

    def test_accepts_localized_metadata(self):
        value = [
            {
                "file_id": "8a1e1c2b-0000-4000-8000-000000000001",
                "name": "plan.pdf",
                "altText": {"en": {"value": "A plan", "direction": "ltr"}},
            }
        ]
        self.assertIsNone(ArchesJsonImporter._check_file_list_shape(value))

    def test_rejects_bare_string_metadata(self):
        value = [
            {
                "file_id": "8a1e1c2b-0000-4000-8000-000000000001",
                "name": "plan.pdf",
                "altText": "A plan",
            }
        ]
        error = ArchesJsonImporter._check_file_list_shape(value)
        self.assertIsNotNone(error)
        self.assertIn("altText", str(error))

    def test_rejects_missing_file_id(self):
        self.assertIsNotNone(
            ArchesJsonImporter._check_file_list_shape([{"name": "plan.pdf"}])
        )

    def test_rejects_non_list(self):
        self.assertIsNotNone(
            ArchesJsonImporter._check_file_list_shape({"name": "plan.pdf"})
        )

    def test_allows_absent_and_null_metadata(self):
        value = [
            {
                "file_id": "8a1e1c2b-0000-4000-8000-000000000001",
                "name": "plan.pdf",
                "altText": None,
            }
        ]
        self.assertIsNone(ArchesJsonImporter._check_file_list_shape(value))


class ConstraintCandidateTests(SimpleTestCase):
    NODE = "3f0f1a44-0000-4000-8000-00000000000a"
    NODEGROUP = "9c2b7d10-0000-4000-8000-00000000000b"

    def _graph(self):
        return {"constraints": {self.NODEGROUP: [[self.NODE]]}}

    def test_same_value_on_two_resources_collides(self):
        candidates = {}
        for resourceid in ("resource-1", "resource-2"):
            ArchesJsonImporter._collect_constraint_candidates(
                {"nodegroup_id": self.NODEGROUP, "data": {self.NODE: "REF-001"}},
                resourceid,
                self._graph(),
                candidates,
            )
        group = candidates[(self.NODEGROUP, (self.NODE,))]
        self.assertEqual(len(group), 1, "one distinct value expected")
        self.assertEqual(sorted(next(iter(group.values()))), ["resource-1", "resource-2"])

    def test_distinct_values_do_not_collide(self):
        candidates = {}
        for resourceid, ref in (("resource-1", "REF-001"), ("resource-2", "REF-002")):
            ArchesJsonImporter._collect_constraint_candidates(
                {"nodegroup_id": self.NODEGROUP, "data": {self.NODE: ref}},
                resourceid,
                self._graph(),
                candidates,
            )
        group = candidates[(self.NODEGROUP, (self.NODE,))]
        self.assertEqual(len(group), 2)
        for resourceids in group.values():
            self.assertEqual(len(resourceids), 1)

    def test_null_value_is_not_a_candidate(self):
        candidates = {}
        ArchesJsonImporter._collect_constraint_candidates(
            {"nodegroup_id": self.NODEGROUP, "data": {self.NODE: None}},
            "resource-1",
            self._graph(),
            candidates,
        )
        self.assertEqual(candidates.get((self.NODEGROUP, (self.NODE,)), {}), {})


class ValidationMemoTests(SimpleTestCase):
    """The memo pays for itself on low-cardinality nodes and is pure leak on free
    text; without the cap a large load retains every distinct string it sees."""

    NODE = "3f0f1a44-0000-4000-8000-00000000000a"

    def test_memo_is_dropped_for_high_cardinality_nodes(self):
        importer = _bare_importer()
        node = {"datatype": "string", "config": {}}

        # Stub the datatype so the test stays free of graph/database setup.
        class _NoErrors:
            def validate(self, value, **kwargs):
                return []

        class _Factory:
            def get_instance(self, datatype):
                return _NoErrors()

        importer.datatype_factory = _Factory()

        for i in range(MEMO_MAX_VALUES_PER_NODE + 5):
            importer._validate_value(node, self.NODE, f"value-{i}")

        self.assertIn(self.NODE, importer._memo_disabled_nodes)
        self.assertNotIn(self.NODE, importer.validated_data)

    def test_memo_returns_cached_errors(self):
        importer = _bare_importer()
        node = {"datatype": "string", "config": {}}
        calls = []

        class _CountingDatatype:
            def validate(self, value, **kwargs):
                calls.append(value)
                return [{"title": "bad", "message": "bad"}]

        class _Factory:
            def get_instance(self, datatype):
                return _CountingDatatype()

        importer.datatype_factory = _Factory()

        first = importer._validate_value(node, self.NODE, "repeated")
        second = importer._validate_value(node, self.NODE, "repeated")
        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1, "second call should be served from the memo")


class BatchedDbCheckTests(SimpleTestCase):
    """The set-based existence checks only run when overwrite is off. Unbatched,
    a 331k-resource import asked Postgres about ~4.2M tiles in one statement -
    three arrays of that many UUIDs - and the load died reporting the generic
    "Unable to read file"."""

    def test_batches_cover_everything_in_order(self):
        items = list(range(4_200_000))
        batches = list(ArchesJsonImporter._in_batches(items))
        self.assertEqual([x for b in batches for x in b], items)
        self.assertLessEqual(max(len(b) for b in batches), DB_CHECK_BATCH_SIZE)

    def test_batch_stays_under_the_bind_parameter_limit(self):
        # legacyid__in becomes one bind parameter per value; Postgres caps at 65,535.
        self.assertLess(DB_CHECK_BATCH_SIZE, 65535)

    def test_empty_input_runs_no_query(self):
        self.assertEqual(list(ArchesJsonImporter._in_batches([])), [])

    def test_a_dict_batches_by_key(self):
        # _check_global_constraints batches the candidate-value dict this way.
        keys = {f"k{i}": i for i in range(5)}
        self.assertEqual(
            list(ArchesJsonImporter._in_batches(keys, 2)),
            [["k0", "k1"], ["k2", "k3"], ["k4"]],
        )
