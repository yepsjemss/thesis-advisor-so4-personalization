"""Small, explicit query-type router; no generation or RAG calls."""
import re

QUERY_TYPES = {'auto': 'Detect automatically', 'eligibility': 'Course eligibility',
               'prerequisites': 'Prerequisite lookup', 'plan': 'Validate my plan',
               'retake': 'Retakes', 'remaining_units': 'Remaining units'}


def classify_query(text):
    t = text.casefold()
    for kind, phrases in (
        ('remaining_units', ('remaining units', 'units left', 'units remaining', 'ilang units', 'natitirang units')),
        ('retake', ('retake', 'failed', 'bagsak')),
        ('prerequisites', ('prerequisite', 'prereq', 'co-requisite', 'corequisite', 'required before')),
        ('plan', ('my plan', 'current plan', 'validate plan', 'planned subjects', 'overload', 'unit limit')),
        ('eligibility', ('eligible', 'can i take', 'can i enroll', 'can i enrol', 'what can i take', 'pwede')),
    ):
        if any(p in t for p in phrases): return kind
    return 'unknown'


def answer(graph, records, selected, question, *, kind='auto', year_level=1, max_units=21, waivers=()):
    kind = classify_query(question) if kind == 'auto' else kind
    context = dict(year_level=year_level, max_units=max_units, waivers=waivers)
    totals = graph.unit_totals(records)
    result = {'query_type': kind, 'curriculum': graph.curriculum, 'remaining_units': totals['remaining'],
              'mode': 'Deterministic SO4 rules', 'courses': [], 'validation': None}
    mentioned = [code for code, c in graph.courses.items() if any(
        re.search(r'(?<![A-Z0-9])' + re.escape(alias) + r'(?![A-Z0-9])', question.upper())
        for alias in {code, c.display_code, *c.aliases})]
    if kind == 'remaining_units':
        result['reply'] = f"You have {totals['remaining']:g} of {totals['total']:g} units remaining. Accepted completed courses account for {totals['completed']:g} units."
    elif kind == 'retake':
        codes = [c.display_code for c in graph.courses.values() if records.get(c.code) == 'failed']
        result['reply'] = 'Courses marked failed: ' + (', '.join(codes) if codes else 'none') + '. Retake plans still undergo prerequisite and load validation.'
    elif kind == 'plan':
        v = graph.validate(records, selected, **context); result['validation'] = v
        result['reply'] = f"Your draft contains {v['units']:g} units against a {v['limit']:g}-unit limit. " + ('Validation passed.' if v['valid'] else 'Resolve the validation issues below.')
    elif kind == 'prerequisites':
        if not mentioned:
            result['reply'] = 'Include a curriculum course code, for example: prerequisites for CALENG2.'
        else:
            lines = []
            for code in mentioned:
                rules = graph.incoming[code]
                lines.append(graph.courses[code].display_code + ': ' + (', '.join(e['kind'] + ': ' + e['source'] for e in rules) or 'No listed course requirements.'))
            result['reply'] = '\n'.join(lines)
    elif kind == 'eligibility':
        if mentioned:
            v = graph.validate(records, set(selected) | set(mentioned), **context)
            result['validation'] = v
            result['reply'] = '\n'.join(graph.courses[c].display_code + ': ' + ('prerequisite checks passed' if v['courses'][c]['eligible'] else '; '.join(v['courses'][c]['reasons'])) for c in mentioned)
            if v['load_errors']: result['reply'] += '\n' + '\n'.join(v['load_errors'])
        else:
            codes = [c.code for c in graph.courses.values() if records.get(c.code) not in {'completed', 'enrolled'}
                     and graph.validate(records, set(selected) | {c.code}, **context)['valid']]
            result['courses'] = codes
            result['reply'] = 'Courses that pass checks with your current draft: ' + (', '.join(graph.courses[c].display_code for c in codes) or 'none. Resolve your existing draft or pending requirements first.')
    else:
        result['reply'] = 'Choose a question type above, or ask about eligibility, prerequisites, your plan, retakes, or remaining units.'
    result['reply'] += '\nBased on your saved curriculum/progress; actual offerings and official enrollment remain subject to institutional confirmation.'
    return result
