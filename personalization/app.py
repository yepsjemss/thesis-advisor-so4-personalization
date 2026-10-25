"""Local Flask/MySQL SO4 workspace. The original RAG demo is never imported."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps
from pathlib import Path
import csv
import io
import json
import os
import re
import secrets

from dotenv import load_dotenv
from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, send_file, session, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint, select
from sqlalchemy.orm import selectinload
from werkzeug.security import check_password_hash, generate_password_hash

from .accuracy import FIELDS, compute_accuracy, parse_csv
from .queries import QUERY_TYPES, answer
from .rules import Course, CurriculumGraph, equivalent_options

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(__file__).resolve().parent / 'data'
db = SQLAlchemy()


def now(): return datetime.now(timezone.utc).replace(tzinfo=None)


class User(db.Model):
    __tablename__ = 'so4_users'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(190), nullable=False, unique=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.Enum('student', 'professor'), nullable=False)
    student_number = db.Column(db.String(8), unique=True)
    curriculum = db.Column(db.String(24))
    year_level = db.Column(db.Integer, default=1, nullable=False)
    professor_id = db.Column(db.Integer, db.ForeignKey('so4_users.id'))
    professor_code = db.Column(db.String(12), unique=True)
    approved_limit = db.Column(db.Numeric(5, 1))
    created_at = db.Column(db.DateTime, default=now)


class Curriculum(db.Model):
    __tablename__ = 'so4_curricula'
    key = db.Column(db.String(24), primary_key=True)
    program = db.Column(db.String(8), nullable=False)
    batch = db.Column(db.Integer, nullable=False)
    source = db.Column(db.String(160), nullable=False)


class CourseRecord(db.Model):
    __tablename__ = 'so4_courses'
    id = db.Column(db.Integer, primary_key=True)
    curriculum = db.Column(db.String(24), db.ForeignKey('so4_curricula.key'), nullable=False)
    code = db.Column(db.String(40), nullable=False)
    title = db.Column(db.String(300), nullable=False)
    units = db.Column(db.Numeric(5, 1), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    term = db.Column(db.Integer, nullable=False)
    source = db.Column(db.String(160), nullable=False)
    page = db.Column(db.Integer, default=0)
    needs_review = db.Column(db.Boolean, default=False)
    warnings = db.Column(db.JSON, nullable=False, default=list)
    rules = db.relationship('Relationship', cascade='all, delete-orphan', lazy='selectin')
    __table_args__ = (UniqueConstraint('curriculum', 'code'),)


class Relationship(db.Model):
    __tablename__ = 'so4_relationships'
    id = db.Column(db.Integer, primary_key=True)
    target_id = db.Column(db.Integer, db.ForeignKey('so4_courses.id'), nullable=False)
    source_code = db.Column(db.String(80), nullable=False)
    kind = db.Column(db.String(24), nullable=False)
    __table_args__ = (UniqueConstraint('target_id', 'source_code', 'kind'),)


class Progress(db.Model):
    __tablename__ = 'so4_progress'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('so4_users.id'), nullable=False)
    course_id = db.Column(db.Integer, db.ForeignKey('so4_courses.id'), nullable=False)
    status = db.Column(db.String(20), nullable=False)
    verified = db.Column(db.Boolean, default=False)
    __table_args__ = (UniqueConstraint('user_id', 'course_id'),)


class Choice(db.Model):
    __tablename__ = 'so4_choices'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('so4_users.id'), nullable=False)
    course_id = db.Column(db.Integer, db.ForeignKey('so4_courses.id'), nullable=False)
    selected_code = db.Column(db.String(40), nullable=False)
    kind = db.Column(db.String(16), nullable=False)
    decision = db.Column(db.String(16), default='pending', nullable=False)
    __table_args__ = (UniqueConstraint('user_id', 'course_id'),)


class PlanCourse(db.Model):
    __tablename__ = 'so4_plan_courses'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('so4_users.id'), nullable=False)
    course_id = db.Column(db.Integer, db.ForeignKey('so4_courses.id'), nullable=False)
    __table_args__ = (UniqueConstraint('user_id', 'course_id'),)


class AcademicReview(db.Model):
    __tablename__ = 'so4_academic_reviews'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('so4_users.id'), nullable=False)
    professor_id = db.Column(db.Integer, db.ForeignKey('so4_users.id'), nullable=False)
    action = db.Column(db.String(24), nullable=False)
    course = db.Column(db.String(40), default='')
    required = db.Column(db.String(80), default='')
    note = db.Column(db.String(2000), nullable=False)
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=now)


class Submission(db.Model):
    __tablename__ = 'so4_submissions'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('so4_users.id'), nullable=False)
    course_codes = db.Column(db.JSON, nullable=False)
    context = db.Column(db.JSON, nullable=False)
    state = db.Column(db.String(24), default='submitted', nullable=False)
    note = db.Column(db.String(2000), default='')
    created_at = db.Column(db.DateTime, default=now)


class QueryLog(db.Model):
    __tablename__ = 'so4_queries'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('so4_users.id'), nullable=False)
    question = db.Column(db.String(2000), nullable=False)
    query_type = db.Column(db.String(24), nullable=False)
    response = db.Column(db.JSON, nullable=False)
    profile_snapshot = db.Column(db.JSON, nullable=False)
    created_at = db.Column(db.DateTime, default=now)


PROGRAMS = {'CPE': 'Computer Engineering', 'ECE': 'Electronics Engineering', 'EES': 'Electrical Engineering'}
STATUSES = {'not_taken': 'Not taken', 'completed': 'Completed', 'enrolled': 'Currently enrolled', 'failed': 'Failed / retake'}


def catalog_rows(): return json.loads((DATA / 'curricula.json').read_text(encoding='utf-8'))
def ge_choices(): return json.loads((DATA / 'ge_choices.json').read_text(encoding='utf-8'))


def seed_catalog():
    """Add a fresh catalog. Never overwrite public rules or student records."""
    if db.session.scalar(select(Curriculum.key).limit(1)): return
    rows = catalog_rows()
    for key in sorted({r['curriculum_key'] for r in rows}):
        row = next(r for r in rows if r['curriculum_key'] == key)
        db.session.add(Curriculum(key=key, program=row['program'], batch=row['batch_id'], source=row['source']))
    db.session.flush()
    for row in rows:
        course = CourseRecord(curriculum=row['curriculum_key'], code=row['course_code'], title=row['title'],
                              units=row['units'], year=row['year_level'], term=row['term_number'], source=row['source'],
                              page=row.get('source_page', 0), needs_review=row['needs_review'], warnings=row['data_warnings'])
        course.rules = [Relationship(source_code=p['course_code'], kind=p['type']) for p in row['prerequisites']]
        db.session.add(course)
    db.session.commit()


def context_for(user):
    records = db.session.scalars(select(CourseRecord).where(CourseRecord.curriculum == user.curriculum)
                                 .order_by(CourseRecord.year, CourseRecord.term, CourseRecord.id)).all()
    choices = {c.course_id: c for c in db.session.scalars(select(Choice).where(Choice.user_id == user.id)).all()}
    grades = {p.course_id: p for p in db.session.scalars(select(Progress).where(Progress.user_id == user.id)).all()}
    ges = {c['code']: c for c in ge_choices()}
    courses = []
    for row in records:
        c = Course(row.code, row.title, row.units, row.year, row.term, row.source, row.page or 0,
                   row.needs_review, list(row.warnings), [{'course_code': r.source_code, 'type': r.kind} for r in row.rules])
        c.aliases = equivalent_options(user.curriculum, c)
        choice = choices.get(row.id)
        if choice:
            c.display_code = choice.selected_code
            c.credit_pending = choice.decision != 'approved'
            if choice.kind == 'ge' and choice.selected_code in ges:
                ge = ges[choice.selected_code]
                c.title = ge['title']
                c.warnings = [w for w in c.warnings if 'placeholder/generic' not in w]
                c.review = bool(c.warnings)
                c.rules = list({(r['type'], r['course_code']): r for r in c.rules + ge['rules']}.values())
        courses.append(c)
    graph = CurriculumGraph(user.curriculum, courses)
    byid = {r.id: r.code for r in records}
    statuses = {byid[k]: p.status for k, p in grades.items() if k in byid}
    selected = {byid[p.course_id] for p in db.session.scalars(select(PlanCourse).where(PlanCourse.user_id == user.id)).all() if p.course_id in byid}
    waivers = [(r.course, r.required) for r in db.session.scalars(select(AcademicReview).where(
        AcademicReview.student_id == user.id, AcademicReview.action == 'rule_confirmation', AcademicReview.active.is_(True))).all()]
    limit = user.approved_limit or Decimal(os.getenv('SO4_DEFAULT_UNIT_LIMIT', '21'))
    kwargs = dict(year_level=user.year_level, max_units=limit, waivers=waivers)
    return dict(graph=graph, statuses=statuses, selected=selected, kwargs=kwargs,
                totals=graph.unit_totals(statuses), validation=graph.validate(statuses, selected, **kwargs),
                course_rows=records, bycode={r.code: r for r in records}, grades=grades, choices=choices)


def student_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not g.user: return redirect(url_for('home'))
        if g.user.role != 'student': abort(403)
        return fn(*args, **kwargs)
    return wrapped


def professor_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not g.user: return redirect(url_for('home'))
        if g.user.role != 'professor': abort(403)
        return fn(*args, **kwargs)
    return wrapped


def linked_student(ident):
    user = db.get_or_404(User, ident)
    if user.role != 'student' or user.professor_id != g.user.id: abort(404)
    return user


def create_app(config=None):
    load_dotenv(ROOT / '.env.personalization', interpolate=False)
    app = Flask(__name__, template_folder='templates', static_folder='static')
    app.config.update(SECRET_KEY=os.getenv('SO4_SESSION_KEY') or secrets.token_hex(32),
                      SQLALCHEMY_DATABASE_URI=os.getenv('SO4_DATABASE_URL', ''),
                      SQLALCHEMY_TRACK_MODIFICATIONS=False, MAX_CONTENT_LENGTH=1024 * 1024,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax')
    if config: app.config.update(config)
    uri = app.config['SQLALCHEMY_DATABASE_URI']
    if not uri.startswith('mysql+pymysql://'):
        raise RuntimeError('Configure SO4_DATABASE_URL in .env.personalization using configure_so4.bat. Only MySQL is supported.')
    db.init_app(app)

    @app.before_request
    def current_user():
        g.user = db.session.get(User, session.get('user_id')) if session.get('user_id') else None
        session.setdefault('csrf', secrets.token_urlsafe(24))
        if request.method == 'POST' and not secrets.compare_digest(session['csrf'], request.form.get('csrf', '')):
            abort(400, 'The form expired. Reload and try again.')

    @app.context_processor
    def template_context():
        return {'user': g.get('user'), 'programs': PROGRAMS, 'progress_labels': STATUSES, 'query_types': QUERY_TYPES,
                'curricula': db.session.scalars(select(Curriculum).order_by(Curriculum.key)).all()}

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(413)
    def error_page(error): return render_template('error.html', error=error), error.code

    @app.get('/')
    def home():
        if g.user: return redirect(url_for('dashboard'))
        return render_template('home.html')

    @app.post('/signup')
    def signup():
        role = request.form.get('role')
        name = request.form.get('name', '').strip(); email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        curriculum = db.session.get(Curriculum, request.form.get('curriculum', ''))
        number = request.form.get('student_number', '').strip()
        errors = []
        if role not in {'student', 'professor'}: errors.append('Choose Student or Professor.')
        if not 2 <= len(name) <= 100: errors.append('Enter a name between 2 and 100 characters.')
        if not re.fullmatch(r'[^\s@]+@dlsu\.edu\.ph', email): errors.append('Use your @dlsu.edu.ph email address.')
        if len(password) < 8 or len(password) > 128: errors.append('Use a password between 8 and 128 characters.')
        if db.session.scalar(select(User).where(User.email == email)): errors.append('That email is already in use.')
        if role == 'student':
            if not curriculum: errors.append('Choose a supported curriculum.')
            if not re.fullmatch(r'[0-9]{8}', number): errors.append('Student number must contain exactly 8 digits.')
            elif curriculum and not number.startswith(str(curriculum.batch)): errors.append('The first three student-number digits must match your curriculum ID.')
            if db.session.scalar(select(User).where(User.student_number == number)): errors.append('That student number is already in use.')
        try: year = int(request.form.get('year_level', 1))
        except ValueError: year = -1
        if year not in {0, 1, 2, 3, 4}: errors.append('Choose a year level, including Other if applicable.')
        if errors:
            for error in errors: flash(error, 'error')
            return render_template('home.html', signup_active=True), 400
        person = User(name=name, email=email, role=role, password_hash=generate_password_hash(password),
                      curriculum=curriculum.key if role == 'student' else None,
                      student_number=number if role == 'student' else None, year_level=year,
                      professor_code=secrets.token_hex(4).upper() if role == 'professor' else None)
        db.session.add(person); db.session.commit()
        session.clear(); session['user_id'] = person.id
        flash('Account created. Welcome to your workspace.', 'success')
        return redirect(url_for('dashboard'))

    @app.post('/login')
    def login():
        email = request.form.get('email', '').strip().lower()
        person = db.session.scalar(select(User).where(User.email == email))
        if not person or not check_password_hash(person.password_hash, request.form.get('password', '')):
            flash('Invalid email or password.', 'error'); return redirect(url_for('home'))
        session.clear(); session['user_id'] = person.id
        return redirect(url_for('dashboard'))

    @app.post('/logout')
    def logout():
        session.clear(); flash('Signed out. Your academic progress is saved.', 'success')
        return redirect(url_for('home'))

    @app.get('/dashboard')
    def dashboard():
        if not g.user: return redirect(url_for('home'))
        if g.user.role == 'professor':
            students = db.session.scalars(select(User).where(User.professor_id == g.user.id).order_by(User.name)).all()
            return render_template('professor.html', students=students)
        ctx = context_for(g.user)
        ready = [c for c in ctx['graph'].courses.values() if ctx['statuses'].get(c.code) not in {'completed', 'enrolled'}
                 and ctx['graph'].validate(ctx['statuses'], ctx['selected'] | {c.code}, **ctx['kwargs'])['valid']]
        return render_template('dashboard.html', **ctx, ready=ready)

    @app.get('/planner')
    @student_required
    def planner(): return render_template('planner.html', **context_for(g.user), ge_choices=ge_choices())

    @app.post('/progress')
    @student_required
    def save_progress():
        rows = db.session.scalars(select(CourseRecord).where(CourseRecord.curriculum == g.user.curriculum)).all()
        existing = {p.course_id: p for p in db.session.scalars(select(Progress).where(Progress.user_id == g.user.id)).all()}
        changes = []
        for c in rows:
            status = request.form.get('status_' + str(c.id), 'not_taken')
            if status not in STATUSES: abort(400, 'Choose a valid course status.')
            if status != (existing[c.id].status if c.id in existing else 'not_taken'): changes.append((c, status))
        for c, status in changes:
            record = existing.get(c.id)
            if record is None: record = Progress(user_id=g.user.id, course_id=c.id); db.session.add(record)
            record.status = status; record.verified = False
        db.session.commit(); flash(f'Progress saved for {len(changes)} courses. Units and eligibility updated.', 'success')
        return redirect(url_for('planner'))

    @app.post('/choices/<int:course_id>')
    @student_required
    def save_choice(course_id):
        row = db.get_or_404(CourseRecord, course_id)
        if row.curriculum != g.user.curriculum: abort(404)
        value = request.form.get('selected_code', '')
        c = Course(row.code, row.title, row.units, row.year, row.term)
        ge = {c['code']: c for c in ge_choices() if Decimal(str(c['units'])) == row.units}
        is_ge = row.code.startswith('LCC') and value in ge
        if value != row.code and not is_ge and value not in equivalent_options(g.user.curriculum, c): abort(400, 'Choose an offered alternative for this slot.')
        if is_ge:
            other = db.session.scalar(select(Choice).where(Choice.user_id == g.user.id, Choice.selected_code == value, Choice.course_id != row.id))
            if other: abort(400, 'This GE subject is already selected for another slot.')
        choice = db.session.scalar(select(Choice).where(Choice.user_id == g.user.id, Choice.course_id == row.id))
        if value == row.code:
            if choice: db.session.delete(choice)
        else:
            if choice is None: choice = Choice(user_id=g.user.id, course_id=row.id); db.session.add(choice)
            choice.selected_code = value; choice.kind = 'ge' if is_ge else 'equivalent'; choice.decision = 'pending'
        db.session.commit(); flash('Subject choice saved. Alternative credit requires your linked professor’s confirmation.', 'success')
        return redirect(url_for('planner'))

    @app.post('/plan')
    @student_required
    def save_plan():
        ctx = context_for(g.user); codes = request.form.getlist('courses')
        if len(codes) != len(set(codes)) or not set(codes) <= set(ctx['graph'].courses): abort(400, 'Choose curriculum subjects once each.')
        PlanCourse.query.filter_by(user_id=g.user.id).delete()
        for code in codes: db.session.add(PlanCourse(user_id=g.user.id, course_id=ctx['bycode'][code].id))
        db.session.commit(); flash('Draft saved. Review its prerequisite and load checks before submission.', 'success')
        return redirect(url_for('plan_page'))

    @app.get('/plan')
    @student_required
    def plan_page():
        return render_template('plan.html', **context_for(g.user), submissions=db.session.scalars(select(Submission).where(Submission.student_id == g.user.id).order_by(Submission.id.desc())).all())

    @app.post('/plan/submit')
    @student_required
    def submit_plan():
        ctx = context_for(g.user)
        if not ctx['selected'] or not ctx['validation']['valid']: abort(400, 'Resolve all draft requirements and unit limits before submitting.')
        if not g.user.professor_id: abort(400, 'Link your professor in Profile before submitting.')
        db.session.add(Submission(student_id=g.user.id, course_codes=sorted(ctx['selected']), context={
            'curriculum': g.user.curriculum, 'year_level': g.user.year_level, 'remaining_units': ctx['totals']['remaining'],
            'records': ctx['statuses'], 'validation': ctx['validation']}))
        db.session.commit(); flash('Validated plan submitted to your linked professor.', 'success')
        return redirect(url_for('plan_page'))

    @app.get('/graph')
    def graph_page():
        if not g.user: return redirect(url_for('home'))
        if g.user.role == 'student': ctx = context_for(g.user)
        else:
            key = request.args.get('curriculum', 'CPE_ID125')
            if not db.session.get(Curriculum, key): abort(404)
            rows = db.session.scalars(select(CourseRecord).where(CourseRecord.curriculum == key)).all()
            graph = CurriculumGraph(key, [Course(r.code, r.title, r.units, r.year, r.term, r.source, r.page or 0,
                r.needs_review, r.warnings, [{'course_code': p.source_code, 'type': p.kind} for p in r.rules]) for r in rows])
            ctx = {'graph': graph, 'statuses': {}}
        payload = ctx['graph'].payload(ctx['statuses'])
        return render_template('graph.html', payload=payload, graph=ctx['graph'])

    @app.get('/graph.json')
    @student_required
    def graph_json():
        ctx = context_for(g.user); return jsonify(ctx['graph'].payload(ctx['statuses']))

    @app.get('/questions')
    @student_required
    def questions():
        return render_template('questions.html', logs=db.session.scalars(select(QueryLog).where(QueryLog.user_id == g.user.id).order_by(QueryLog.id.desc()).limit(30)).all(), **context_for(g.user))

    @app.post('/questions')
    @student_required
    def question():
        text = request.form.get('question', '').strip(); kind = request.form.get('query_type', 'auto')
        if not 1 <= len(text) <= 2000 or kind not in QUERY_TYPES: abort(400, 'Enter a question and choose a supported type.')
        ctx = context_for(g.user)
        response = answer(ctx['graph'], ctx['statuses'], ctx['selected'], text, kind=kind, **ctx['kwargs'])
        db.session.add(QueryLog(user_id=g.user.id, question=text, query_type=response['query_type'], response=response,
                                profile_snapshot={'degree_program': g.user.curriculum.split('_')[0], 'curriculum': g.user.curriculum,
                                    'completed_courses': [c for c, s in ctx['statuses'].items() if s == 'completed'],
                                    'remaining_units': ctx['totals']['remaining'], 'year_level': g.user.year_level}))
        db.session.commit(); return redirect(url_for('questions'))

    @app.get('/profile')
    def profile():
        if not g.user: return redirect(url_for('home'))
        professor = db.session.get(User, g.user.professor_id) if g.user.professor_id else None
        return render_template('profile.html', professor=professor)

    @app.post('/profile')
    @student_required
    def update_profile():
        try: year = int(request.form.get('year_level', ''))
        except ValueError: abort(400, 'Choose a year level.')
        if year not in {0, 1, 2, 3, 4}: abort(400)
        code = request.form.get('professor_code', '').strip().upper()
        professor = db.session.scalar(select(User).where(User.role == 'professor', User.professor_code == code)) if code else None
        if code and not professor: abort(400, 'Professor code not found. Ask your professor for their code.')
        if g.user.professor_id != (professor.id if professor else None):
            # A new professor must independently reconfirm student-specific concessions.
            AcademicReview.query.filter_by(student_id=g.user.id).update({'active': False})
            g.user.approved_limit = None
            Choice.query.filter_by(user_id=g.user.id).update({'decision': 'pending'})
            Progress.query.filter_by(user_id=g.user.id).update({'verified': False})
        g.user.professor_id = professor.id if professor else None; g.user.year_level = year
        db.session.commit(); flash('Profile saved. Eligibility has been recalculated.', 'success')
        return redirect(url_for('profile'))

    @app.get('/students/<int:student_id>')
    @professor_required
    def student_detail(student_id):
        person = linked_student(student_id); ctx = context_for(person)
        return render_template('student.html', student=person, **ctx,
            submissions=db.session.scalars(select(Submission).where(Submission.student_id == person.id).order_by(Submission.id.desc())).all(),
            reviews=db.session.scalars(select(AcademicReview).where(AcademicReview.student_id == person.id).order_by(AcademicReview.id.desc())).all())

    @app.post('/students/<int:student_id>/review')
    @professor_required
    def review_student(student_id):
        person = linked_student(student_id); ctx = context_for(person)
        action = request.form.get('action'); note = request.form.get('note', '').strip()
        if not 5 <= len(note) <= 2000: abort(400, 'Include a review note/reference of 5–2000 characters.')
        course = request.form.get('course', ''); required = request.form.get('required', '')
        if action == 'load_limit':
            try: limit = Decimal(request.form.get('limit', ''))
            except InvalidOperation: abort(400, 'Enter a unit limit.')
            if not limit.is_finite() or limit < 1 or limit > 40 or limit * 10 != int(limit * 10): abort(400, 'Choose a limit from 1 to 40 units, at most one decimal place.')
            person.approved_limit = limit
        elif action == 'rule_confirmation':
            c = ctx['graph'].courses.get(course)
            allowed = bool(c) and ((required == '*' and c.review) or any(e['kind'] == 'S' and e['source'] == required for e in ctx['graph'].incoming[course]))
            if not allowed: abort(400, 'Only flagged entries and soft-policy confirmations can be reviewed here. Hard prerequisites cannot be waived.')
            AcademicReview.query.filter_by(student_id=person.id, course=course, required=required, action=action).update({'active': False})
        elif action == 'choice':
            row = ctx['bycode'].get(course)
            choice = ctx['choices'].get(row.id) if row else None
            if not choice or request.form.get('decision') not in {'approved', 'rejected'}: abort(400)
            choice.decision = request.form['decision']
        elif action == 'progress':
            row = ctx['bycode'].get(course); record = ctx['grades'].get(row.id) if row else None
            if not record: abort(400)
            record.verified = True
        elif action == 'plan':
            try: submission = db.session.get(Submission, int(request.form.get('submission', '')))
            except ValueError: abort(400)
            if not submission or submission.student_id != person.id or submission.state != 'submitted': abort(404)
            decision = request.form.get('decision')
            if decision not in {'approved', 'revision_requested'}: abort(400)
            if decision == 'approved' and not ctx['graph'].validate(ctx['statuses'], submission.course_codes, **ctx['kwargs'])['valid']:
                abort(400, 'The saved submission no longer passes current rules/progress/load checks.')
            submission.state = decision; submission.note = note
        else: abort(400)
        db.session.add(AcademicReview(student_id=person.id, professor_id=g.user.id, action=action, course=course, required=required, note=note))
        db.session.commit(); flash('Academic review saved. Student validation will use the updated context.', 'success')
        return redirect(url_for('student_detail', student_id=person.id))

    @app.route('/accuracy', methods=['GET', 'POST'])
    def accuracy_page():
        if not g.user: return redirect(url_for('home'))
        report = compute_accuracy([]); error = None
        if request.method == 'POST':
            upload = request.files.get('decisions')
            try:
                if not upload: raise ValueError('Choose a decisions CSV file.')
                report = compute_accuracy(parse_csv(upload.read().decode('utf-8-sig')))
            except (ValueError, UnicodeDecodeError) as exc: error = str(exc)
        return render_template('accuracy.html', report=report, error=error)

    @app.get('/accuracy/template.csv')
    def accuracy_template():
        if not g.user: return redirect(url_for('home'))
        out = io.StringIO(); writer = csv.writer(out); writer.writerow(FIELDS)
        writer.writerow(['EL-001', 'eligibility', '', '', 'Fill official checklist/page', 'false'])
        writer.writerow(['CV-001', 'constraint', '', '', 'Fill approved unit policy/reference', 'false'])
        return app.response_class(out.getvalue(), mimetype='text/csv', headers={'Content-Disposition': 'attachment; filename=so4-decisions-template.csv'})

    @app.get('/checklists/<key>')
    def checklist(key):
        if not g.user: return redirect(url_for('home'))
        cur = db.get_or_404(Curriculum, key)
        candidates = list((ROOT / 'DATA/raw_data').rglob(cur.source))
        if len(candidates) != 1: abort(404)
        return send_file(candidates[0], mimetype='application/pdf')

    @app.get('/healthz')
    def health(): return jsonify(status='ok', mode='SO4 rules only', rag_loaded=False)

    @app.cli.command('init-db')
    def init_db():
        db.create_all(); seed_catalog(); print('SO4 MySQL tables and public curriculum graph initialized. No RAG calls.')

    return app
