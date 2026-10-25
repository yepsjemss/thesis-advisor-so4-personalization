"""Synthetic engineering checks, NOT a formal academic accuracy dataset."""
import unittest
from personalization.rules import Course, CurriculumGraph, equivalent_options
from personalization.queries import answer, classify_query
from personalization.accuracy import compute_accuracy, parse_csv


def graph():
    return CurriculumGraph('SYNTHETIC', [
        Course('FOUND', 'Synthetic foundation', 3, 1, 1),
        Course('NEXT', 'Synthetic dependent', 3, 1, 2, rules=[{'type': 'H', 'course_code': 'FOUND'}]),
        Course('LAB', 'Synthetic concurrent lab', 1, 1, 2, rules=[{'type': 'C', 'course_code': 'NEXT'}]),
        Course('SOFT', 'Synthetic soft policy', 3, 2, 1, rules=[{'type': 'S', 'course_code': 'FOUND'}]),
        Course('ZERO', 'Synthetic zero unit requirement', 0, 1, 1),
        Course('ZDEP', 'Synthetic zero unit dependent', 2, 1, 2, rules=[{'type': 'H', 'course_code': 'ZERO'}]),
    ])


class RulesTests(unittest.TestCase):
    def test_hard_prerequisite_and_completed_duplicate(self):
        g = graph()
        self.assertFalse(g.validate({}, {'NEXT'})['valid'])
        self.assertTrue(g.validate({'FOUND': 'completed'}, {'NEXT'})['valid'])
        self.assertFalse(g.validate({'FOUND': 'failed'}, {'NEXT'})['valid'])
        self.assertFalse(g.validate({'FOUND': 'completed'}, {'FOUND'})['valid'])

    def test_corequisite_pair_and_its_own_requirements(self):
        g = graph()
        self.assertFalse(g.validate({}, {'LAB'})['valid'])
        self.assertFalse(g.validate({}, {'LAB', 'NEXT'})['valid'])
        self.assertTrue(g.validate({'FOUND': 'completed'}, {'LAB', 'NEXT'})['valid'])
        self.assertTrue(g.validate({'NEXT': 'enrolled'}, {'LAB'})['valid'])

    def test_soft_policy_confirmation_cannot_waive_hard(self):
        g = graph()
        self.assertFalse(g.validate({'FOUND': 'failed'}, {'SOFT'})['valid'])
        self.assertTrue(g.validate({'FOUND': 'failed'}, {'SOFT'}, waivers=[('SOFT', 'FOUND')])['valid'])
        self.assertFalse(g.validate({}, {'NEXT'}, waivers=[('NEXT', 'FOUND')])['valid'])

    def test_load_boundary_and_unknown_course(self):
        g = graph()
        self.assertTrue(g.validate({'FOUND': 'completed'}, {'NEXT', 'LAB'}, max_units=4)['valid'])
        self.assertFalse(g.validate({'FOUND': 'completed'}, {'NEXT', 'LAB'}, max_units=3.9)['valid'])
        self.assertFalse(g.validate({}, {'OUTSIDE'})['valid'])
        with self.assertRaises(ValueError): g.validate({}, set(), max_units=0)
        with self.assertRaises(ValueError): g.validate({}, set(), max_units='NaN')

    def test_zero_unit_prerequisite_and_new_accounts(self):
        g = graph()
        self.assertFalse(g.validate({}, {'ZDEP'})['valid'])
        self.assertTrue(g.validate({'ZERO': 'completed'}, {'ZDEP'})['valid'])
        self.assertEqual(g.unit_totals({'ZERO': 'completed'})['completed'], 0)
        for year in (0, 1, 2, 3, 4):
            self.assertEqual(g.validate({}, set(), year_level=year)['remaining_units'], 12)

    def test_pending_credit_does_not_satisfy_or_earn_units(self):
        g = graph(); g.courses['FOUND'].credit_pending = True
        self.assertFalse(g.validate({'FOUND': 'completed'}, {'NEXT'})['valid'])
        self.assertEqual(g.unit_totals({'FOUND': 'completed'})['completed'], 0)
        g.courses['FOUND'].credit_pending = False
        self.assertTrue(g.validate({'FOUND': 'completed'}, {'NEXT'})['valid'])

    def test_standing_review_flags_and_unresolved_reference(self):
        g = CurriculumGraph('SYNTHETIC', [Course('STAND', 'Standing', 3, 3, 1, rules=[{'type': 'standing', 'course_code': 'Year 3'}]),
            Course('REVIEW', 'Review', 1, 1, 1, review=True),
            Course('EXT', 'External', 1, 1, 1, rules=[{'type': 'H', 'course_code': 'MISSING'}])])
        self.assertFalse(g.validate({}, {'STAND'}, year_level=0)['valid'])
        self.assertTrue(g.validate({}, {'STAND'}, year_level=3)['valid'])
        self.assertFalse(g.validate({}, {'REVIEW'})['valid'])
        self.assertTrue(g.validate({}, {'REVIEW'}, waivers=[('REVIEW', '*')])['valid'])
        self.assertFalse(g.validate({}, {'EXT'}, waivers=[('EXT', '*')])['valid'])
        self.assertIn('MISSING', [n['id'] for n in g.payload({})['nodes'] if n['external']])

    def test_corrected_equivalent_group_and_alias_credit(self):
        for cur in (f'{p}_ID{b}' for p in ('CPE', 'ECE') for b in range(122, 126)):
            for code in ('LBYEC3F', 'LBYCPC3', 'LBYCPCS'):
                c = Course(code, 'Synthetic equivalent', 1, 3, 1)
                self.assertEqual(set(equivalent_options(cur, c)), {'LBYEC3F', 'LBYCPC3', 'LBYCPCS'})
            self.assertEqual(equivalent_options(cur, Course('LBYCPC4', 'Separate elective lab', 1, 3, 1)), ())
        c = Course('LBYCPC3', 'Control lab', 1, 3, 1, aliases=('LBYEC3F', 'LBYCPC3', 'LBYCPCS'))
        g = CurriculumGraph('CPE_ID122', [c, Course('DEP', 'Synthetic dependent', 1, 4, 1, rules=[{'type': 'H', 'course_code': 'LBYEC3F'}])])
        self.assertTrue(g.validate({'LBYCPC3': 'completed'}, {'DEP'})['valid'])

    def test_duplicate_selected_credit_blocked(self):
        g = CurriculumGraph('SYNTHETIC', [Course('LCC1', 'Slot one', 3, 1, 1, display_code='GEONE'), Course('LCC2', 'Slot two', 3, 1, 2, display_code='GEONE')])
        self.assertFalse(g.validate({}, {'LCC1', 'LCC2'})['valid'])

    def test_query_types_and_saved_context(self):
        phrases = {'Can I take NEXT?': 'eligibility', 'What prerequisites for NEXT?': 'prerequisites',
                   'Validate my plan': 'plan', 'Which retakes do I have?': 'retake', 'Ilang units left?': 'remaining_units'}
        for text, kind in phrases.items(): self.assertEqual(classify_query(text), kind)
        self.assertEqual(classify_query('Hello there'), 'unknown')
        result = answer(graph(), {'FOUND': 'completed'}, set(), 'Can I take NEXT?')
        self.assertTrue(result['validation']['valid']); self.assertEqual(result['remaining_units'], 9)
        self.assertEqual(answer(graph(), {}, set(), 'hello', kind='remaining_units')['query_type'], 'remaining_units')


class AccuracyTests(unittest.TestCase):
    def test_no_observations_no_percentage(self):
        report = compute_accuracy([])
        self.assertEqual(report['status'], 'not_evaluated')
        self.assertIsNone(report['metrics']['eligibility']['accuracy_percent'])
        self.assertIsNone(report['metrics']['constraint']['meets_95_percent'])

    def test_unverified_or_blank_labels_keep_unmeasured(self):
        for row in [dict(expected='', actual='', expected_verified=True, source_reference='Synthetic'),
                    dict(expected=True, actual=True, expected_verified=False, source_reference='Synthetic')]:
            report = compute_accuracy([dict(case_id='X', decision_type='eligibility', **row)])
            self.assertEqual(report['status'], 'not_evaluated')
            self.assertIsNone(report['metrics']['eligibility']['accuracy_percent'])

    def test_synthetic_confusion_matrix_arithmetic_only(self):
        rows = [dict(case_id=str(i), decision_type='eligibility', expected=e, actual=a,
                     expected_verified=True, source_reference='Synthetic engineering fixture')
                for i, (e, a) in enumerate([(True, True), (False, False), (True, False), (False, True)])]
        m = compute_accuracy(rows)['metrics']['eligibility']
        self.assertEqual(m['accuracy_percent'], 50)
        self.assertEqual([m[x] for x in ('tp', 'tn', 'fp', 'fn')], [1, 1, 1, 1])
        self.assertFalse(m['meets_95_percent'])

    def test_invalid_duplicates_and_partial_upload(self):
        row = dict(case_id='X', decision_type='constraint', expected=True, actual=False, expected_verified=True, source_reference='Synthetic')
        with self.assertRaises(ValueError): compute_accuracy([row, row])
        with self.assertRaises(ValueError): parse_csv('wrong,columns\n1,2')
        with self.assertRaises(ValueError): compute_accuracy({'not': 'a list'})
        with self.assertRaises(ValueError): compute_accuracy(['not a record'])
        report = compute_accuracy([row, dict(row, case_id='P', actual='')])
        self.assertIsNone(report['metrics']['constraint']['accuracy_percent'])


if __name__ == '__main__': unittest.main()
