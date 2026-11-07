# EVA audit experiment · baseline and rollout gate

Frozen before changing the evaluator. Parent a8ee9d3c3f942422849adbf2d85087b5c9fc7f75, clean main. The production baseline is consultation-v1.1, not the older plain ISSAI or the synthetic UI fixture. FastAPI/llama.cpp b12186, Qwen3.5-9B-Kazakh.Q5_K_M (6,566,423,936 bytes), seed 42, temperature 0, thinking off, context 16384, maximum completion 3200. RTX 4070 12282 MiB, system RAM 8 GiB. Existing local services on 5173/8000/8001 belong to the running team environment. No client data or model outputs go in Git.

| Frozen input | SHA-256 |
|---|---|
| System prompt UTF-8 | 69f170e7fc6a65241f0125eb78a748104ad9a29dc4a9a5462170960cc5c369b3 |
| Checklist fixture bytes | 279028302e625beccc596c2a9d39aa0fb5747121528f6018eb1bd08f7b181ee2 |
| Fact table | 59af9c1ae0f7563bbcb1f77ba56e02036be3e8d3b06375d58dd9bd5193dbc41e |
| ISSAI provider source (working tree) | 45ec3fa741f78e0e7cde638c253fe42aa667221207c62b9b7b1f83f4828ee7a4 |
| Consultation provider source | 5724e6d9021e0059ae25e79a65978755301b5d6eb0c8001edde96d12377605f1 |
| Preserved baseline outputs, 15 conversations | e7c43752c83194259135235bdd8a5a61ef4d5aedc3dd295c8072fd2cb51e541c |
| Adjudicated reference, 120 rows | 2b492a71caeb10a4dc113a6ac3b2dc03cca818b758da51e84b83fd1e0a149ce4 |

Reference and output locations are in the ignored ml/data/blind15-comparison-20261108 and ml/data/consultation-v1.1/sealed folders. Recorded current baseline: 89/120 exact, 17 correct N/A, 7 false and 7 missed N/A, 3 uncertain rows, 12/15 numerical totals. Development pilot: 78/96 exact, median 7.427 s, maximum 12.252 s. Baseline includes the targeted consultation recheck. Published trial groups are fixed; translations/variants stay in one group. These are previously examined chats, not a fresh holdout. Five real call transcripts form an unlabeled pilot unless separately annotated. Do not read/tune against failures during model development.

**Activation gate (predeclared):** more exact human criterion decisions with abstentions counted as nonmatches, no additional false gross accusations or missed annotated violations, no loss of coverage or increase of final-score error on common scored chats, all technical references valid, manual review of semantic adequacy, new languages and minimal pairs checked. Require sufficient independent human examples for all three violations; absent positives cannot qualify a candidate. Warm p95 below the host backend deadline (20 s by default), no GPU OOM. A shared-GPU measurement is indicative. Shadow/development only otherwise.

An existing HTTP 201 confirms persistence, not quality. The baseline differentiates N/A from review. The UI demo is synthetic; local inference and append-only review operate on real transcripts. ASR is not a dependency of this experiment.
