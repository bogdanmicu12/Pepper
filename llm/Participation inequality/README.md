# Participation Inequality

This folder computes adjusted Gini coefficients for participation inequality in
Pepper transcript CSV files.

It can analyze any transcript with the live transcript columns used by the
bridge:

- `event_type`
- `speaker`
- `text`
- `start_timestamp`
- `end_timestamp`
- optional `phase`

Only participant rows are included. Robot rows are ignored.

## What It Computes

For each transcript, the script computes adjusted Gini coefficients for:

- `word_count`
- `turn_count`
- `speech_time_seconds`

If phases exist in the transcript, the default behavior is to compute each
metric for every phase plus `overall`. For a transcript with `divergence` and
`convergence`, this gives 9 coefficients.

If the transcript has no phases, it computes one `overall` table with 3
coefficients.

The adjusted Gini coefficient is:

```text
adjusted_gini = standard_gini / ((n - 1) / n)
```

For two participants, this simplifies to:

```text
adjusted_gini = abs(participant_1_value - participant_2_value) / total_value
```

## Run The Two Demo Transcripts

From the repository root:

```powershell
python "llm\Participation inequality\run_adjusted_gini.py" --demo
```

This writes two separate tables into a new timestamped folder under
`llm\Participation inequality\results\`.

It does not create one combined results table.

## Run One Specific Transcript

Use this for your actual data later:

```powershell
python "llm\Participation inequality\run_adjusted_gini.py" `
  --transcript "path\to\your\transcript.csv" `
  --dataset-name "my_actual_session"
```

Every run creates a new run folder, so previous result tables are not
overwritten.

## Recommended Measure

My recommendation is to treat `speech_time_seconds` as the best primary measure
when timestamps are reliable, because it captures actual floor time. Use
`word_count` as a strong secondary check for contribution volume. Use
`turn_count` cautiously, because a short backchannel and a long explanation both
count as one turn.
