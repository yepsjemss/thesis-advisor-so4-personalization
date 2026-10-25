"""Typed curriculum knowledge graph and deterministic student-specific validation.

The graph is an adjacency structure built from MySQL course/relationship tables.
It is both the validator's rule source and the interactive diagram's data source.
No Neo4j, vector database, LLM, or model downloads are used here.
"""
from dataclasses import dataclass, field
from decimal import Decimal
import re


@dataclass
class Course:
    code: str
    title: str
    units: Decimal
    year: int
    term: int
    source: str = ''
    page: int = 0
    review: bool = False
    warnings: list = field(default_factory=list)
    rules: list = field(default_factory=list)
    display_code: str = ''
    credit_pending: bool = False
    aliases: tuple = ()

    def __post_init__(self):
        self.units = Decimal(str(self.units))
        self.display_code = self.display_code or self.code


EQUIVALENTS = (
    (('FDCNSYS', 'FDCONSY', 'FDCONSYS', 'FEECONS'), 3),
    (('DSIGPRO', 'SIGDISC'), 3),
    (('LOGDSGN', 'CIRLOGI'), 3),
    (('LBYEC3A', 'LBYEC3B'), 1),
    (('LBYCPA4', 'LBYEC4A'), 1),
    (('LBYEC3F', 'LBYCPC3', 'LBYCPCS'), 1),
)


def equivalent_options(curriculum, course):
    if curriculum not in {f'{p}_ID{b}' for p in ('CPE', 'ECE') for b in range(122, 126)}:
        return ()
    for codes, units in EQUIVALENTS:
        if course.code in codes and course.units == units:
            return codes
    return ()


class CurriculumGraph:
    def __init__(self, curriculum, courses):
        self.curriculum = curriculum
        self.courses = {c.code: c for c in courses}
        if len(self.courses) != len(courses):
            raise ValueError('Duplicate curriculum course code.')
        self.aliases = {}
        for c in courses:
            for alias in (c.code, c.display_code, *c.aliases):
                self.aliases.setdefault(alias, c.code)
        # Exact curriculum slots take precedence over alternative names.
        self.aliases.update({c.code: c.code for c in courses})
        self.edges = [dict(source=r['course_code'], target=c.code, kind=r['type'],
                           reference=c.source, page=c.page)
                      for c in courses for r in c.rules]
        self.incoming = {code: [] for code in self.courses}
        self.outgoing = {}
        for edge in self.edges:
            self.incoming[edge['target']].append(edge)
            self.outgoing.setdefault(edge['source'], []).append(edge)

    def names(self, codes):
        result = set(codes)
        for code in codes:
            if code in self.courses:
                c = self.courses[code]
                result.update((c.display_code, *c.aliases))
        return result

    def unit_totals(self, records):
        total = sum((c.units for c in self.courses.values()), Decimal(0))
        completed = sum((c.units for c in self.courses.values()
                         if records.get(c.code) == 'completed' and not c.credit_pending), Decimal(0))
        return {'total': float(total), 'completed': float(completed),
                'remaining': float(max(total - completed, Decimal(0)))}

    def validate(self, records, selected, *, year_level=1, max_units=21, waivers=()):
        selected = set(selected)
        limit = Decimal(str(max_units))
        if not limit.is_finite() or limit <= 0:
            raise ValueError('A positive, finite unit limit is required.')
        passed = self.names({k for k, v in records.items() if v == 'completed'
                            and k in self.courses and not self.courses[k].credit_pending})
        enrolled = self.names({k for k, v in records.items() if v == 'enrolled'
                              and k in self.courses and not self.courses[k].credit_pending})
        concurrent = self.names(selected)
        waivers = set(tuple(w) for w in waivers)
        results = {}
        for code in sorted(selected):
            course = self.courses.get(code)
            reasons, warnings = [], []
            if course is None:
                reasons.append('Course is outside this curriculum.')
            else:
                if records.get(code) == 'completed': reasons.append('Already completed.')
                if records.get(code) == 'enrolled': reasons.append('Currently enrolled.')
                if course.credit_pending: reasons.append('Selected alternative credit needs professor confirmation.')
                if course.review and (code, '*') not in waivers:
                    reasons.append('Checklist entry needs professor verification.')
                for edge in self.incoming[code]:
                    req, kind = edge['source'], edge['kind']
                    if kind == 'H' and req not in passed:
                        reasons.append(f'Pass {req} before taking {course.display_code}.')
                    elif kind == 'C' and req not in passed | enrolled | concurrent:
                        reasons.append(f'Complete or take {req} alongside {course.display_code}.')
                    elif kind == 'S' and req not in passed and (code, req) not in waivers:
                        reasons.append(f'Soft prerequisite {req} needs professor confirmation of prior enrollment/policy.')
                    elif kind == 'standing':
                        match = re.search(r'\d+', req)
                        if not match or not year_level or year_level < int(match.group()):
                            reasons.append(f'Requires confirmed {req}.')
                    elif kind not in {'H', 'C', 'S', 'standing'}:
                        reasons.append(f'Unresolved requirement {req}: professor verification required.')
                warnings.extend(course.warnings)
            results[code] = {'eligible': not reasons, 'reasons': reasons, 'warnings': warnings}
        units = sum((self.courses[c].units for c in selected if c in self.courses), Decimal(0))
        load_errors = []
        if units > limit: load_errors.append(f'Plan has {units:g} units; your configured/approved limit is {limit:g}.')
        # Two slots cannot claim the same selected subject for credit.
        displays = [self.courses[c].display_code for c in selected if c in self.courses]
        if len(displays) != len(set(displays)):
            load_errors.append('A selected subject cannot fill two curriculum slots in one plan.')
        return {'valid': not load_errors and all(r['eligible'] for r in results.values()),
                'courses': results, 'units': float(units), 'limit': float(limit),
                'load_errors': load_errors, 'remaining_units': self.unit_totals(records)['remaining'],
                'curriculum': self.curriculum, 'backend': 'MySQL adjacency graph'}

    def payload(self, records):
        nodes = [{'id': c.code, 'label': c.display_code, 'title': c.title, 'units': float(c.units),
                  'year': c.year, 'term': c.term, 'review': c.review or c.credit_pending,
                  'status': records.get(c.code, 'not_taken'), 'source': c.source, 'external': False}
                 for c in self.courses.values()]
        missing = sorted({e['source'] for e in self.edges} - set(self.aliases))
        nodes.extend({'id': code, 'label': code, 'title': 'External/unresolved checklist requirement',
                      'units': 0, 'year': 0, 'term': 0, 'review': True,
                      'status': 'unverified', 'source': '', 'external': True} for code in missing)
        edges = [dict(e, source=self.aliases.get(e['source'], e['source']), source_label=e['source'])
                 for e in self.edges]
        return {'curriculum': self.curriculum, 'backend': 'MySQL adjacency graph', 'nodes': nodes, 'edges': edges}
