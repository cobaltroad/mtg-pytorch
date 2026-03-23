<#
.SYNOPSIS
    Inspect raw synergy edges for a card directly from the training artifact.

.DESCRIPTION
    Loads the artifact (.pt file) without requiring a checkpoint and displays:
      - Positive synergy pairs where the card is the consumer (triggered by others)
      - Positive synergy pairs where the card is the producer (enables others)
      - Functional equivalence pairs the card belongs to
      - Card metadata (type line, CMC, color identity)

    Useful for diagnosing bad synergy edges before training, without needing to
    run through a trained encoder.

.PARAMETER Card
    Card name to query.  Partial / case-insensitive match accepted.

.PARAMETER TrainingPath
    Which artifact to inspect: 'compositional' (default) or 'cooccurrence'.

.PARAMETER Top
    Number of pairs to display per section (default 20).

.PARAMETER Dataset
    Override the artifact path.

.EXAMPLE
    .\scripts\eval_artifact.ps1 "Sythis, Harvest's Hand"

.EXAMPLE
    .\scripts\eval_artifact.ps1 "Impact Tremors" -Top 30

.EXAMPLE
    .\scripts\eval_artifact.ps1 "Garruk's Uprising" -TrainingPath cooccurrence
#>

param(
    [Parameter(Mandatory)]
    [string]$Card,

    [ValidateSet('cooccurrence', 'compositional')]
    [string]$TrainingPath = 'compositional',

    [int]$Top = 20,

    [string]$Dataset = ''
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')

if (-not (Test-Path "$RepoRoot\.venv\Scripts\python.exe")) {
    throw "Missing .venv. Create it with: py -3.12 -m venv .venv"
}

if (-not $Dataset) {
    $artifactName = if ($TrainingPath -eq 'compositional') {
        'mtg_dataset_compositional.pt'
    } else {
        'mtg_dataset.pt'
    }
    $Dataset = Join-Path $RepoRoot "ingest_cache\$artifactName"
}

if (-not (Test-Path $Dataset)) {
    Write-Error "Artifact not found: $Dataset"
    exit 1
}

# Write the Python script to a temp file so card names with apostrophes are safe
$pyScript = Join-Path $env:TEMP 'eval_artifact_tmp.py'

Set-Content -Path $pyScript -Encoding UTF8 -Value @'
import sys, torch, argparse

parser = argparse.ArgumentParser()
parser.add_argument('card')
parser.add_argument('--dataset', required=True)
parser.add_argument('--top', type=int, default=20)
args = parser.parse_args()

artifact = torch.load(args.dataset, map_location='cpu', weights_only=False)
query = args.card.lower()

card_ids   = artifact['card_ids']
card_meta  = artifact['card_meta']

idx_to_cid  = {i: str(cid.item() if hasattr(cid, 'item') else cid) for i, cid in enumerate(card_ids)}
idx_to_meta = {i: card_meta.get(idx_to_cid[i], {}) for i in idx_to_cid}
idx_to_name = {i: m.get('name', f'id:{idx_to_cid[i]}') for i, m in idx_to_meta.items()}

# Resolve query card
matches = [(i, n) for i, n in idx_to_name.items() if query in n.lower()]
if not matches:
    print(f'No card matching "{args.card}" in artifact.')
    sys.exit(1)
if len(matches) > 1:
    exact = [(i, n) for i, n in matches if n.lower() == query]
    matches = exact if exact else matches
    if len(matches) > 1:
        print(f'Ambiguous ({len(matches)} matches): ' + ', '.join(n for _, n in matches[:10]))
        sys.exit(1)

card_idx, card_name = matches[0]
meta = idx_to_meta[card_idx]

print()
print('=' * 70)
print(f'  {card_name}')
print('=' * 70)
print(f'  Type:           {meta.get("type_line", "?")}')
print(f'  CMC:            {meta.get("cmc", "?")}')
print(f'  Color identity: {meta.get("color_identity", "?")}')
print(f'  Artifact idx:   {card_idx}')
print()

syn = artifact['synergy']
a_t = syn['a_idx']
b_t = syn['b_idx']
l_t = syn['labels']
top = args.top

# ── As consumer: b == card_idx ───────────────────────────────────────────────
mask_c    = (b_t == card_idx) & (l_t > 0.5)
c_total   = mask_c.sum().item()
c_prods   = a_t[mask_c]
print(f'As CONSUMER (triggered by) — {c_total} positive pairs  [top {min(top, c_total)}]')
for pidx in c_prods[:top]:
    pm = idx_to_meta.get(pidx.item(), {})
    print(f'  {idx_to_name.get(pidx.item(), "?"):45s}  {pm.get("type_line", "")[:30]}')
print()

# ── As producer: a == card_idx ───────────────────────────────────────────────
mask_p    = (a_t == card_idx) & (l_t > 0.5)
p_total   = mask_p.sum().item()
p_cons    = b_t[mask_p]
print(f'As PRODUCER (enables) — {p_total} positive pairs  [top {min(top, p_total)}]')
for cidx in p_cons[:top]:
    pm = idx_to_meta.get(cidx.item(), {})
    print(f'  {idx_to_name.get(cidx.item(), "?"):45s}  {pm.get("type_line", "")[:30]}')
print()

# ── Functional equivalence pairs ─────────────────────────────────────────────
fp = artifact.get('functional_pairs')
if fp is not None and isinstance(fp, dict) and 'a_idx' in fp:
    fp_a = fp['a_idx']; fp_b = fp['b_idx']
    mask_fp = (fp_a == card_idx) | (fp_b == card_idx)
    partners = [
        (fp_b[i].item() if fp_a[i].item() == card_idx else fp_a[i].item())
        for i in mask_fp.nonzero(as_tuple=True)[0].tolist()
    ]
    print(f'Functional equivalence pairs — {len(partners)} partners  [top {min(top, len(partners))}]')
    for pidx in partners[:top]:
        pm = idx_to_meta.get(pidx, {})
        print(f'  {idx_to_name.get(pidx, "?"):45s}  {pm.get("type_line", "")[:30]}')
else:
    print('No functional_pairs in artifact.')
print()
'@

& "$RepoRoot\.venv\Scripts\python.exe" -u $pyScript $Card --dataset $Dataset --top $Top

exit $LASTEXITCODE
