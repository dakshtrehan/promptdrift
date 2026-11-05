# Source of benchmark/data/pairs.jsonl

`pairs.jsonl` holds 200 sentence pairs sampled from the **test split of the
Microsoft Research Paraphrase Corpus (MRPC)**: 100 pairs labelled as
paraphrases (`label: 1`) and 100 labelled as not paraphrases (`label: 0`).

* Original corpus: Dolan, W. B. and Brockett, C. (2005). "Automatically
  Constructing a Corpus of Sentential Paraphrases." Third International
  Workshop on Paraphrasing (IWP2005). Published by Microsoft Research.
* The file was obtained from a public GitHub mirror of the original
  `msr_paraphrase_test.txt` (1725 pairs) because the proxy in the build
  environment refused connections to huggingface.co and to the GLUE
  download host. The mirror used was
  `https://raw.githubusercontent.com/wasiahmad/paraphrase_identification/master/dataset/msr-paraphrase-corpus/msr_paraphrase_test.txt`.
* Sampling: pairs containing the characters U+2013 or U+2014 were dropped
  (this repository's docs forbid them), then `random.Random(0).sample` chose
  100 positives and 100 negatives, which were shuffled with the same seed.
  The MRPC test split is 68 percent paraphrases, so the sample is
  rebalanced on purpose to make AUC and threshold accuracy easy to read.
* Each record: `id` (MRPC sentence ids), `sentence_a`, `sentence_b`,
  `label` (1 paraphrase, 0 not), `source`.

## Licence

MRPC is distributed by Microsoft Research for research purposes under the
Microsoft Research Data License Agreement for the Microsoft Research
Paraphrase Corpus. The sentences are short extracts of news articles. This
200 pair sample is included only so the benchmark is reproducible; it is not
covered by this repository's MIT licence and should not be used for anything
beyond evaluating text similarity methods. If you redistribute it, keep this
notice.
