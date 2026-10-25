"""Optional MySQL integration checks; use ONLY an isolated so4_test_ database.

No real student records, RAG scripts or model APIs are used. These checks are not
the independently verified academic accuracy study described by SO4.
"""
import os
import re
import sys
import unittest
from sqlalchemy.engine import make_url
from sqlalchemy import select
from werkzeug.security import generate_password_hash
from personalization.app import create_app, db, seed_catalog, User, Curriculum, CourseRecord, Progress, Choice, Submission, QueryLog


@unittest.skipUnless(os.getenv('SO4_TEST_DATABASE_URL'), 'Requires an isolated local MySQL test database.')
class ApplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = os.environ['SO4_TEST_DATABASE_URL']; parts = make_url(url)
        if parts.host not in {'127.0.0.1', 'localhost'} or not parts.database.startswith('so4_test_') or parts.port in {3306, 3307}:
            raise RuntimeError('Refusing tests outside an isolated loopback MySQL database/port.')
        cls.app = create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': url, 'SECRET_KEY': 'synthetic-session-only'})
        cls.counter = 0; cls.pw = 'SyntheticTest!2026'; cls.hash = generate_password_hash(cls.pw)
        with cls.app.app_context(): db.create_all(); seed_catalog()

    def setUp(self):
        type(self).counter += 1; n = type(self).counter
        with self.app.app_context():
            professor = User(name='Synthetic Professor', email=f'prof{n}@dlsu.edu.ph', role='professor', password_hash=self.hash, professor_code=f'PTEST{n:03}')
            other = User(name='Other Synthetic Professor', email=f'other{n}@dlsu.edu.ph', role='professor', password_hash=self.hash, professor_code=f'OTHER{n:03}')
            db.session.add_all([professor, other]); db.session.flush()
            student = User(name='Synthetic Student', email=f'student{n}@dlsu.edu.ph', role='student', student_number=f'125{n:05}',
                           curriculum='CPE_ID125', year_level=4, professor_id=professor.id, password_hash=self.hash)
            db.session.add(student); db.session.commit()
            self.sid, self.pid, self.oid = student.id, professor.id, other.id
        self.student = self.client(self.sid); self.prof = self.client(self.pid); self.other = self.client(self.oid)

    def client(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as s: s['user_id'] = user_id; s['csrf'] = 'synthetic-csrf'
        return client

    def post(self, client, url, data=None): return client.post(url, data=dict(data or {}, csrf='synthetic-csrf'), follow_redirects=False)

    def test_pages_and_no_rag_or_neo4j_import(self):
        for url in ('/dashboard', '/planner', '/plan', '/graph', '/questions', '/profile', '/accuracy', '/graph.json'):
            response = self.student.get(url)
            self.assertEqual(response.status_code, 200, (url, response.data[:300]))
        self.assertEqual(self.prof.get('/graph').status_code, 200)
        self.assertEqual(self.prof.get('/dashboard').status_code, 200)
        self.assertEqual(self.prof.get(f'/students/{self.sid}').status_code, 200)
        self.assertFalse(any(k.split('.')[0] in {'openai', 'transformers', 'chromadb', 'neo4j'} for k in sys.modules))

    def test_year_four_starts_with_all_units_and_bulk_save(self):
        with self.app.app_context():
            rows = db.session.scalars(select(CourseRecord).where(CourseRecord.curriculum == 'CPE_ID125')).all()
            foundation = next(r for r in rows if r.code == 'FNDMATH')
            nextcourse = next(r for r in rows if r.code == 'CALENG1')
            fields = {'status_' + str(r.id): 'not_taken' for r in rows}
            fields['status_' + str(foundation.id)] = 'completed'
            fields['status_' + str(nextcourse.id)] = 'failed'
        self.assertIn(b'209.0', self.student.get('/dashboard').data)
        self.assertEqual(self.post(self.student, '/progress', fields).status_code, 302)
        with self.app.app_context():
            ps = db.session.scalars(select(Progress).where(Progress.user_id == self.sid)).all()
            self.assertEqual(len(ps), 2)
        self.assertEqual(self.post(self.student, '/questions', {'question': 'How many remaining units?', 'query_type': 'auto'}).status_code, 302)
        with self.app.app_context():
            q = db.session.scalar(select(QueryLog).where(QueryLog.user_id == self.sid))
            self.assertEqual(q.query_type, 'remaining_units')
            self.assertEqual(q.profile_snapshot['remaining_units'], 204)

    def test_invalid_plan_cannot_submit_and_cross_role_access(self):
        self.assertEqual(self.post(self.student, '/plan', {'courses': ['CALENG1']}).status_code, 302)
        self.assertEqual(self.post(self.student, '/plan/submit').status_code, 400)
        self.assertEqual(self.student.get(f'/students/{self.sid}').status_code, 403)
        self.assertEqual(self.other.get(f'/students/{self.sid}').status_code, 404)
        self.assertEqual(self.prof.get('/planner').status_code, 403)

    def test_valid_plan_review_rechecks_current_limit(self):
        self.assertEqual(self.post(self.student, '/plan', {'courses': ['BASCHEM', 'PHYSC01']}).status_code, 302)
        self.assertEqual(self.post(self.student, '/plan/submit').status_code, 302)
        with self.app.app_context(): ident = db.session.scalar(select(Submission.id).where(Submission.student_id == self.sid))
        self.assertEqual(self.post(self.prof, f'/students/{self.sid}/review', {'action': 'load_limit', 'limit': '3', 'note': 'Synthetic approved limit reference'}).status_code, 302)
        self.assertEqual(self.post(self.prof, f'/students/{self.sid}/review', {'action': 'plan', 'submission': str(ident), 'decision': 'approved', 'note': 'Synthetic review'}).status_code, 400)

    def test_equivalency_approval_and_scope(self):
        with self.app.app_context():
            row = db.session.scalar(select(CourseRecord).where(CourseRecord.curriculum == 'CPE_ID125', CourseRecord.code == 'LBYCPCS'))
            ident = row.id
        self.assertEqual(self.post(self.student, f'/choices/{ident}', {'selected_code': 'LBYCPC4'}).status_code, 400)
        self.assertEqual(self.post(self.student, f'/choices/{ident}', {'selected_code': 'LBYCPC3'}).status_code, 302)
        self.assertEqual(self.post(self.other, f'/students/{self.sid}/review', {'action': 'choice', 'course': 'LBYCPCS', 'decision': 'approved', 'note': 'Synthetic credit proof'}).status_code, 404)
        self.assertEqual(self.post(self.prof, f'/students/{self.sid}/review', {'action': 'choice', 'course': 'LBYCPCS', 'decision': 'approved', 'note': 'Synthetic credit proof'}).status_code, 302)
        with self.app.app_context(): self.assertEqual(db.session.scalar(select(Choice).where(Choice.user_id == self.sid)).decision, 'approved')

    def test_accuracy_default_unmeasured_and_template(self):
        page = self.student.get('/accuracy')
        self.assertIn(b'Not evaluated', page.data)
        self.assertNotIn(b'95.0%', page.data)
        template = self.student.get('/accuracy/template.csv')
        self.assertIn(b'expected_verified', template.data)
        self.assertIn(b',false', template.data)

    def test_signup_only_two_roles_and_matching_id(self):
        client = self.app.test_client(); page = client.get('/')
        with client.session_transaction() as s: token = s['csrf']
        data = dict(name='Synthetic New Student', email='newfixture@dlsu.edu.ph', password=self.pw, role='superadmin', curriculum='CPE_ID122', student_number='12499999', csrf=token)
        response = client.post('/signup', data=data)
        self.assertEqual(response.status_code, 400)
        self.assertIn(b'Choose Student or Professor', response.data)
        data['role'] = 'student'
        self.assertEqual(client.post('/signup', data=data).status_code, 400)
        data['student_number'] = '12299999'; data['year_level'] = '0'
        self.assertEqual(client.post('/signup', data=data).status_code, 302)
        self.assertIn(b'209.0', client.get('/dashboard').data)


if __name__ == '__main__': unittest.main()
