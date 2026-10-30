"""Grouped human-reference role comparison; fixed settings, no model inference."""
from __future__ import annotations
from collections import Counter, defaultdict
import re
import numpy as np
from scipy import sparse
from scipy.optimize import minimize
from scipy.special import logsumexp
from .graph.page_representations import clean_text, training_boilerplate
from .page_role_annotation import require, digest


def scores(truth, predicted, classes):
    require(len(truth) == len(predicted) and len(truth) > 0, 'score-join')
    require(set(truth) <= set(classes) and set(predicted) <= set(classes), 'score-class')
    matrix = np.zeros((len(classes), len(classes)), dtype=int)
    index = {v: i for i, v in enumerate(classes)}
    for a, b in zip(truth, predicted):
        matrix[index[a], index[b]] += 1
    per_class = {}
    for i, name in enumerate(classes):
        tp = int(matrix[i, i]); actual = int(matrix[i].sum()); pred = int(matrix[:, i].sum())
        precision = tp/pred if pred else 0.0
        recall = tp/actual if actual else 0.0
        per_class[name] = {'support': actual, 'precision': precision, 'recall': recall,
                           'f1': 2*tp/(actual+pred) if actual+pred else 0.0}
    observed = float(matrix.trace()/matrix.sum())
    expected = float(matrix.sum(axis=0) @ matrix.sum(axis=1)/matrix.sum()**2)
    return {'classes': classes, 'confusion': matrix.tolist(), 'per_class': per_class,
            'macro_f1': float(np.mean([v['f1'] for v in per_class.values()])),
            'balanced_accuracy': float(np.mean([v['recall'] for v in per_class.values()])),
            'accuracy': observed, 'cohen_kappa': (observed-expected)/(1-expected) if expected < 1 else None}


def agreement(human, ai, groups, classes, draws, seed):
    require(set(human) == set(ai) and set(human) <= set(groups), 'agreement-join')
    ids = sorted(v for v in human if human[v] is not None and ai[v] is not None)
    require(ids, 'agreement-no-pairs')
    result = scores([human[v] for v in ids], [ai[v] for v in ids], classes)
    result.update({'paired': len(ids), 'total': len(human), 'unresolved': sorted(set(human)-set(ids)),
                   'human_distribution': dict(Counter('unresolved' if v is None else v for v in human.values())),
                   'ai_distribution': dict(Counter('unresolved' if v is None else v for v in ai.values())),
                   'interpretation': 'single-human reference; kappa is sensitive to prevalence; no inter-human reliability'})
    clustered = defaultdict(list)
    for v in ids:
        clustered[groups[v]].append(v)
    rng = np.random.default_rng(seed); names = sorted(clustered); accuracy = []; kappa = []
    for _ in range(draws):
        sample = [v for i in rng.integers(0, len(names), len(names)) for v in clustered[names[i]]]
        s = scores([human[v] for v in sample], [ai[v] for v in sample], classes)
        accuracy.append(s['accuracy'])
        if s['cohen_kappa'] is not None:
            kappa.append(s['cohen_kappa'])
    result['group_bootstrap'] = {'draws': draws, 'seed': seed, 'groups': len(names),
        'accuracy_ci95': np.quantile(accuracy, [.025, .975]).tolist() if accuracy else None,
        'kappa_ci95': np.quantile(kappa, [.025, .975]).tolist() if kappa else None,
        'undefined_kappa_draws': draws-len(kappa)}
    return result


def grouped_folds(labels, groups, *, folds, seed, minimum_pages, minimum_groups):
    require(set(labels) <= set(groups) and folds >= 2, 'fold-join')
    counts = Counter(labels.values()); classes = sorted(counts)
    require(len(classes) >= 2, 'class-support')
    for name in classes:
        require(counts[name] >= minimum_pages and len({groups[v] for v in labels if labels[v] == name}) >= minimum_groups, 'class-support')
    by_group = defaultdict(list)
    for v in sorted(labels):
        by_group[groups[v]].append(v)
    require(len(by_group) >= folds, 'fold-group-support')
    rng = np.random.default_rng(seed)
    order = sorted(by_group); rng.shuffle(order)
    order.sort(key=lambda g: -len(by_group[g]))
    totals = np.zeros((folds, len(classes)), dtype=int); assignment = {}
    for group in order:
        local = np.array([sum(labels[v] == c for v in by_group[group]) for c in classes])
        options = []
        for f in range(folds):
            candidate = totals.copy(); candidate[f] += local
            imbalance = float(np.var(candidate/np.array([counts[c] for c in classes]), axis=0).sum())
            options.append((imbalance, int(totals[f].sum()), f))
        f = min(options)[2]; totals[f] += local; assignment[group] = f
    result = {v: assignment[groups[v]] for v in sorted(labels)}
    for f in range(folds):
        require({labels[v] for v in labels if result[v] == f} == set(classes) and
                {labels[v] for v in labels if result[v] != f} == set(classes), 'actual-fold-class-support')
    return {'fold_by_id': result, 'classes': classes, 'seed': seed,
            'counts': totals.tolist(), 'label_sha256': digest(labels), 'groups_sha256': digest(groups)}


def _terms(text):
    tokens = re.findall(r'(?u)\b\w\w+\b', text.lower())
    return tokens + [a+' '+b for a, b in zip(tokens, tokens[1:])]


def tfidf(train_texts, test_texts, *, max_features, min_df):
    boilerplate = training_boilerplate(train_texts)
    train = [Counter(_terms(clean_text(t, boilerplate))) for t in train_texts]
    test = [Counter(_terms(clean_text(t, boilerplate))) for t in test_texts]
    df = Counter(t for row in train for t in row)
    terms = sorted((t for t in df if df[t] >= min_df), key=lambda t: (-df[t], t))[:max_features]
    require(terms, 'text-vocabulary-empty')
    terms.sort(); index = {t: i for i, t in enumerate(terms)}
    idf = np.array([1+np.log((1+len(train))/(1+df[t])) for t in terms])
    def transform(rows):
        rr, cc, vv = [], [], []
        for i, row in enumerate(rows):
            for t, n in row.items():
                if t in index:
                    j = index[t]; rr.append(i); cc.append(j); vv.append((1+np.log(n))*idf[j])
        x = sparse.csr_matrix((vv, (rr, cc)), shape=(len(rows), len(terms)))
        norm = np.sqrt(np.asarray(x.multiply(x).sum(axis=1)).ravel()); norm[norm == 0] = 1
        return sparse.diags(1/norm) @ x
    return transform(train), transform(test), {'vocabulary_sha256': digest(terms), 'dimensions': len(terms),
        'training_boilerplate_sha256': digest(sorted(boilerplate)), 'training_documents': len(train)}


def scale(train, test):
    train, test = np.asarray(train, float).copy(), np.asarray(test, float).copy()
    require(train.ndim == test.ndim == 2 and train.shape[1] == test.shape[1], 'feature-shape')
    require(not np.isinf(train).any() and not np.isinf(test).any(), 'feature-infinite')
    # Missing values are represented by separate caller-declared flags; all-missing
    # training columns use zero. Fit every numeric transform on training only.
    valid = np.isfinite(train); count = valid.sum(axis=0)
    mean = np.nansum(train, axis=0)/np.maximum(count, 1)
    for x in (train, test):
        missing = np.where(np.isnan(x)); x[missing] = mean[missing[1]]
    std = train.std(axis=0); std[std == 0] = 1
    return (train-mean)/std, (test-mean)/std


def softmax(train, y, test, classes, *, C, max_iter):
    train, test = sparse.csr_matrix(train), sparse.csr_matrix(test)
    k, d = len(classes), train.shape[1]
    yy = np.array([classes.index(v) for v in y]); n = len(y)
    def objective(theta):
        w = theta[:d*k].reshape(d, k); b = theta[d*k:]
        logits = train @ w + b
        normalizer = logsumexp(logits, axis=1)
        loss = np.sum(normalizer-logits[np.arange(n), yy]) + .5/C*np.sum(w*w)
        delta = np.exp(logits-normalizer[:, None]); delta[np.arange(n), yy] -= 1
        gradient = np.concatenate([(train.T @ delta + w/C).ravel(), delta.sum(axis=0)])
        return loss, gradient
    result = minimize(objective, np.zeros(d*k+k), jac=True, method='L-BFGS-B',
                      options={'maxiter': max_iter, 'ftol': 1e-9, 'gtol': 1e-6})
    require(result.success, 'softmax-nonconvergence:'+str(result.message))
    w = result.x[:d*k].reshape(d, k); b = result.x[d*k:]
    predicted = [classes[i] for i in np.argmax(test @ w+b, axis=1)]
    return predicted, {'success': bool(result.success), 'iterations': int(result.nit),
                       'objective': float(result.fun), 'dimensions': d, 'C': C}


def paired_bootstrap(labels, predictions, groups, classes, contrasts, draws, seed):
    ids = sorted(labels); require(ids and all(set(p) == set(ids) for p in predictions.values()), 'prediction-join')
    by_group = defaultdict(list)
    for v in ids:
        by_group[groups[v]].append(v)
    names = sorted(by_group); rng = np.random.default_rng(seed)
    values = {a+' minus '+b: [] for a, b in contrasts}; skipped = 0
    for _ in range(draws):
        sampled = [v for i in rng.integers(0, len(names), len(names)) for v in by_group[names[i]]]
        if set(labels[v] for v in sampled) != set(classes):
            skipped += 1; continue
        scores_by_arm = {name: scores([labels[v] for v in sampled], [p[v] for v in sampled], classes)['macro_f1']
                         for name, p in predictions.items()}
        for a, b in contrasts:
            values[a+' minus '+b].append(scores_by_arm[a]-scores_by_arm[b])
    return {'draws': draws, 'seed': seed, 'groups': len(names), 'missing_class_draws_skipped': skipped,
            'intervals': {name: np.quantile(v, [.025, .975]).tolist() if v else None for name, v in values.items()}}


def evaluate(labels, groups, texts, simple, kernels, config, *, freeze=None):
    """Call only after human labels and verified kernel rows are joined by ID.
    kernels is arm -> doc_id -> real/imag row; no feature fitting uses test labels.
    Mixed, unknown and unresolved are excluded here but counted in coverage.
    """
    retained = {v: c for v, c in labels.items() if c is not None and c not in config['excluded_roles']}
    counts = Counter(retained.values())
    eligible = {c for c in counts if counts[c] >= config['minimum_pages'] and
                len({groups[v] for v in retained if retained[v] == c}) >= config['minimum_groups']}
    selected = {v: c for v, c in retained.items() if c in eligible}
    coverage = {'total': len(labels), 'resolved_for_supervision': len(retained), 'eligible': len(selected),
                'distribution': dict(Counter('unresolved' if c is None else c for c in labels.values())),
                'unsupported_classes': sorted(set(counts)-eligible)}
    if len(eligible) < 2:
        return {'status': 'unsupported class support', 'coverage': coverage, 'predictions': None}
    try:
        split = grouped_folds(selected, groups, folds=config['folds'], seed=config['seed'],
                              minimum_pages=config['minimum_pages'], minimum_groups=config['minimum_groups'])
    except ValueError as error:
        return {'status': 'unsupported fold feasibility', 'reason': str(error), 'coverage': coverage, 'predictions': None}
    if freeze is not None:
        freeze({'split': split, 'config': config, 'eligible_label_sha256': digest(selected)})
    require(set(selected) <= set(texts) and set(selected) <= set(simple) and
            all(set(selected) <= set(v) for v in kernels.values()), 'evaluation-id-join')
    arms = ['majority', 'text', 'text_simple', *('text_simple_'+name for name in kernels)]
    predictions = {name: {} for name in arms}; fits = []
    for f in range(config['folds']):
        train = sorted(v for v in selected if split['fold_by_id'][v] != f)
        test = sorted(set(selected)-set(train))
        require(not {groups[v] for v in train} & {groups[v] for v in test}, 'train-test-leakage')
        a, b, meta = tfidf([texts[v] for v in train], [texts[v] for v in test],
                           max_features=config['max_features'], min_df=config['min_df'])
        sa, sb = scale([simple[v] for v in train], [simple[v] for v in test])
        matrices = {'text': (a, b), 'text_simple': (sparse.hstack([a, sa]), sparse.hstack([b, sb]))}
        for name, rows in kernels.items():
            ka, kb = scale([rows[v] for v in train], [rows[v] for v in test])
            matrices['text_simple_'+name] = (sparse.hstack([a, sa, ka]), sparse.hstack([b, sb, kb]))
        majority = min(Counter(selected[v] for v in train).items(), key=lambda x: (-x[1], x[0]))[0]
        predictions['majority'].update({v: majority for v in test})
        for arm, (x, z) in matrices.items():
            values, fit = softmax(x, [selected[v] for v in train], z, split['classes'], C=config['C'], max_iter=config['max_iter'])
            predictions[arm].update(zip(test, values)); fits.append({'fold': f, 'arm': arm, 'text_fit': meta, **fit})
    absolute = {name: scores([selected[v] for v in sorted(selected)], [p[v] for v in sorted(selected)], split['classes']) for name, p in predictions.items()}
    differences = {a+' minus '+b: absolute[a]['macro_f1']-absolute[b]['macro_f1'] for a, b in config['contrasts']}
    return {'status': 'evaluated', 'coverage': coverage, 'split': split, 'predictions': predictions,
            'absolute': absolute, 'macro_f1_differences': differences, 'fits': fits,
            'bootstrap': paired_bootstrap(selected, predictions, groups, split['classes'], config['contrasts'], config['draws'], config['seed']),
            'source_group_performance': {g: {arm: scores([selected[v] for v in selected if groups[v] == g],
                [p[v] for v in selected if groups[v] == g], split['classes']) for arm, p in predictions.items()}
                for g in sorted({groups[v] for v in selected})}}
