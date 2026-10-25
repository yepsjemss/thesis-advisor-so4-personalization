"""Compute agreement with independent expected decisions; does not evaluate RAG."""
import argparse
import csv
import io
import json
from pathlib import Path

FIELDS = ('case_id', 'decision_type', 'expected', 'actual', 'source_reference', 'expected_verified')


def boolean(value):
    if value is True or str(value).lower() in {'true', '1'}: return True
    if value is False or str(value).lower() in {'false', '0'}: return False
    raise ValueError('Expected and actual must be true/false (or 1/0).')


def compute_accuracy(rows):
    if not isinstance(rows, list): raise ValueError('Decisions must be a list of records.')
    if len(rows) > 10000: raise ValueError('Use at most 10,000 decisions per report.')
    ids, parsed, pending = set(), [], []
    for row in rows:
        if not isinstance(row, dict): raise ValueError('Each decision must be a record with the required fields.')
        ident = str(row.get('case_id', '')).strip()
        kind = row.get('decision_type')
        if not ident or ident in ids: raise ValueError('Each case_id must be present and unique.')
        ids.add(ident)
        if kind not in {'eligibility', 'constraint'}: raise ValueError('decision_type must be eligibility or constraint.')
        if not row.get('source_reference') or str(row.get('expected_verified', '')).lower() not in {'true', '1'}:
            pending.append(ident); continue
        if row.get('expected') in ('', None) or row.get('actual') in ('', None):
            pending.append(ident); continue
        expected, actual = boolean(row['expected']), boolean(row['actual'])
        parsed.append({'case_id': ident, 'decision_type': kind, 'expected': expected, 'actual': actual})
    metrics = {}
    for kind in ('eligibility', 'constraint'):
        group = [r for r in parsed if r['decision_type'] == kind]
        correct = sum(r['expected'] == r['actual'] for r in group)
        # Partial uploads have no headline accuracy/target claim.
        ready = bool(group) and not pending
        metrics[kind] = {'correct': correct, 'total': len(group),
                         'accuracy_percent': round(100 * correct / len(group), 4) if ready else None,
                         'tp': sum(r['expected'] and r['actual'] for r in group),
                         'tn': sum(not r['expected'] and not r['actual'] for r in group),
                         'fp': sum(not r['expected'] and r['actual'] for r in group),
                         'fn': sum(r['expected'] and not r['actual'] for r in group),
                         'meets_95_percent': (100 * correct / len(group) >= 95) if ready else None}
    status = 'computed_from_supplied_decisions' if parsed and not pending else 'not_evaluated'
    return {'status': status, 'target_percent': 95, 'formula': 'correct decisions / total evaluated decisions * 100',
            'pending_case_ids': pending, 'metrics': metrics,
            'note': 'No API calls. Supplied expected answers must be independently verified against official references. This report does not establish representativeness or RAG quality.'}


def parse_csv(text):
    reader = csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
    if not reader.fieldnames or not set(FIELDS) <= set(reader.fieldnames):
        raise ValueError('CSV must contain: ' + ', '.join(FIELDS))
    return list(reader)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    text = args.input.read_text(encoding='utf-8-sig')
    rows = json.loads(text) if args.input.suffix == '.json' else parse_csv(text)
    report = compute_accuracy(rows)
    result = json.dumps(report, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(result, encoding='utf-8')
    print(result)


if __name__ == '__main__': main()
