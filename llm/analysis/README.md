# Pepper Elicitation Analysis

This folder contains the analysis script for turning elicitation transcripts,
robot intervention logs, manually coded measures, UES-SF responses, and open
feedback codes into CSV summaries and PNG graphs.

## Files

- `generate_elicitation_measurement_graphs.py`: main analysis script.
- `README.md`: this usage guide.

The script writes generated outputs to a folder you choose with `--output-dir`.
Keep raw data, manually coded CSVs, and generated outputs separate so it is clear
which files are source data and which files are produced by the script.

## Analysis Unit

The unit of analysis is one elicitation window.

An elicitation window starts when Pepper finishes an elicitation-strategy prompt.
It ends when the next elicitation-strategy prompt starts. Normal/context prompts
between two elicitation strategies remain inside the earlier strategy's window.

Example:

```text
Elicitation strategy A
normal prompt
normal prompt
normal prompt
normal prompt
Elicitation strategy B
```

Everything after strategy A until strategy B starts belongs to strategy A's
window. If a phase transition happens before the next elicitation strategy, add
an explicit `window_end_time` in the intervention log so the window stops at the
phase transition.

## Runtime

Use the bundled Codex Python runtime if available:

```powershell
& 'C:\Users\bogda\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B `
  'llm\analysis\generate_elicitation_measurement_graphs.py' --help
```

The script uses `pandas`, `numpy`, and `Pillow`. It does not require
`matplotlib`.

## Step 1: Create Input Templates

Run this once to create blank/example CSV schemas:

```powershell
& 'C:\Users\bogda\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B `
  'llm\analysis\generate_elicitation_measurement_graphs.py' `
  --write-templates 'llm\analysis\templates'
```

This creates:

- `transcript_template.csv`
- `intervention_log_template.csv`
- `manual_window_measures_template.csv`
- `ues_sf_responses_template.csv`
- `open_feedback_coded_template.csv`

Copy these templates and fill them with your real study data.

## Step 2: Fill The Transcript CSV

Required columns:

```csv
session_id,speaker,start_time,end_time,text
```

Alternative duration format:

```csv
session_id,speaker,start_time,duration_seconds,text
```

Column meanings:

- `session_id`: unique session/group discussion ID, such as `S01`.
- `speaker`: participant or robot label, such as `P1`, `P2`, `Participant`,
  `Robot`, or `Pepper`.
- `start_time`: speech onset time.
- `end_time`: speech end time.
- `duration_seconds`: use this instead of `end_time` if your transcript has
  start time plus duration.
- `text`: transcript text for that turn.

Accepted time formats:

- ISO datetimes, for example `2026-05-05T10:00:05`.
- Numeric seconds, for example `245.2`.
- Clock times, for example `04:05` or `01:04:05`.

Use the same time basis in the transcript and intervention log. Do not mix
absolute timestamps in one file with relative seconds in the other unless they
refer to the same timeline.

## Step 3: Fill The Intervention Log CSV

Required columns:

```csv
session_id,prompt_id,phase,strategy,prompt_start_time,prompt_end_time
```

Recommended columns:

```csv
session_id,prompt_id,phase,strategy,prompt_type,prompt_start_time,prompt_end_time,window_end_time
```

Column meanings:

- `session_id`: must match the transcript session.
- `prompt_id`: unique ID for the robot prompt, such as `E01`.
- `phase`: `divergence` or `convergence`.
- `strategy`: elicitation strategy name, such as `generative`,
  `elaboration_evidence`, or `perspective_shift`.
- `prompt_type`: use `elicitation` for strategy prompts and `normal` or
  `context_only` for non-strategy prompts.
- `prompt_start_time`: when Pepper started speaking.
- `prompt_end_time`: when Pepper finished speaking.
- `window_end_time`: optional manual override, useful when a phase transition
  should close the window before the next elicitation strategy.

How the script detects elicitation rows:

- If `is_elicitation` exists, values like `1`, `true`, `yes`, or `elicitation`
  are treated as elicitation rows.
- Else, if `prompt_type` contains `elicitation`, those rows are used.
- Else, any row with a non-empty strategy that is not `context_only`, `normal`,
  `baseline`, `none`, or blank is treated as an elicitation row.

## Step 4: Fill Manual Measure Data

Manual measures are needed for the substantive contribution variables that
should not be trusted to automatic AI coding:

- idea fluency
- elaboration depth
- consecutive turns on the same subject

Recommended columns:

```csv
window_id,session_id,prompt_id,phase,strategy,idea_fluency,elaboration_units,elaborated_contribution_count,consecutive_topic_turns,coder_id,notes
```

Column meanings:

- `window_id`: use the generated format `session_id__prompt_id`, for example
  `S01__E01`.
- `session_id`, `prompt_id`, `phase`, `strategy`: match the elicitation window.
- `idea_fluency`: number of distinct, non-redundant, task-relevant ideas.
- `elaboration_units`: number of concrete elaborative units, such as reasons,
  examples, mechanisms, constraints, consequences, implementation details,
  evidence, analogies, or clarifications.
- `elaborated_contribution_count`: number of ideas/thoughts that contain at
  least one elaborative unit.
- `consecutive_topic_turns`: length of the same-subject turn chain for that
  window. Use one consistent rule, such as longest chain per window.
- `coder_id`: human coder identifier.
- `notes`: optional coding notes.

The script also computes `elaboration_units_per_idea` when both
`elaboration_units` and `idea_fluency` are present.

## Step 5: Fill UES-SF Data

Use the UES-SF questionnaire responses as numeric 1-5 values.

Required item columns:

```csv
FA_S1,FA_S2,FA_S3,PU_S1,PU_S2,PU_S3,AE_S1,AE_S2,AE_S3,RW_S1,RW_S2,RW_S3
```

Recommended full schema:

```csv
participant_id,session_id,phase,strategy,FA_S1,FA_S2,FA_S3,PU_S1,PU_S2,PU_S3,AE_S1,AE_S2,AE_S3,RW_S1,RW_S2,RW_S3
```

Scoring behavior:

- `PU_S1`, `PU_S2`, and `PU_S3` are reverse-coded as `6 - raw_score`.
- `focused_attention` is the mean of `FA_S1`, `FA_S2`, and `FA_S3`.
- `perceived_usability` is the mean of the reverse-coded PU items.
- `aesthetic_appeal` is the mean of `AE_S1`, `AE_S2`, and `AE_S3`.
- `reward` is the mean of `RW_S1`, `RW_S2`, and `RW_S3`.
- `ues_sf_total` is the mean of all 12 scored items.

If you administer UES-SF only once after the whole session, set `phase` to
`post_session` and `strategy` to `overall`. If you add strategy-level ratings,
use the relevant strategy names consistently.

## Step 6: Fill Open Feedback Codes

Open feedback is entered after manual qualitative coding.

Recommended columns:

```csv
participant_id,session_id,phase,strategy,question_id,theme,valence,excerpt_or_note
```

Column meanings:

- `participant_id`: participant identifier.
- `session_id`: session/group ID.
- `phase`: use `post_session`, `divergence`, `convergence`, or another
  consistent label.
- `strategy`: strategy referred to by the feedback, or `overall`.
- `question_id`: short ID such as `most_useful`, `least_useful`,
  `most_natural`, `helped_develop`, `disrupted`, or `phase_difference`.
- `theme`: coded theme name.
- `valence`: optional label such as `positive`, `negative`, `mixed`, or
  `neutral`.
- `excerpt_or_note`: short paraphrase, coded note, or a brief permitted quote.

## Step 7: Run The Full Analysis

Example command:

```powershell
& 'C:\Users\bogda\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B `
  'llm\analysis\generate_elicitation_measurement_graphs.py' `
  --transcript 'llm\analysis\data\transcript.csv' `
  --interventions 'llm\analysis\data\intervention_log.csv' `
  --manual-measures 'llm\analysis\data\manual_window_measures.csv' `
  --ues-responses 'llm\analysis\data\ues_sf_responses.csv' `
  --feedback-codes 'llm\analysis\data\open_feedback_coded.csv' `
  --output-dir 'llm\analysis\outputs'
```

You can omit inputs you do not have yet. For example, you can run only the
transcript/intervention part:

```powershell
& 'C:\Users\bogda\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B `
  'llm\analysis\generate_elicitation_measurement_graphs.py' `
  --transcript 'llm\analysis\data\transcript.csv' `
  --interventions 'llm\analysis\data\intervention_log.csv' `
  --output-dir 'llm\analysis\outputs'
```

If you pass `--transcript`, you must also pass `--interventions`, because the
transcript metrics need elicitation windows.

## Output Files

The script writes CSV summaries and PNG charts to `--output-dir`.

Core CSV outputs:

- `elicitation_windows.csv`: constructed windows with start/end times.
- `transcript_window_metrics.csv`: response delay, speaking time, turn count,
  word count, and mean participant turn duration for each window.
- `transcript_summary_by_phase_strategy.csv`: grouped transcript metrics by
  phase and strategy.
- `manual_window_measures_cleaned.csv`: manual measures after numeric cleanup.
- `manual_summary_by_phase_strategy.csv`: grouped idea fluency, elaboration, and
  topic-chain metrics.
- `ues_sf_scores.csv`: raw and scored UES-SF item values plus subscales.
- `ues_sf_summary.csv`: grouped UES-SF summaries.
- `open_feedback_theme_counts.csv`: counts of coded open-feedback themes.
- `run_manifest.json`: list of generated files.

Chart outputs are written to `--output-dir\charts`.

Generated chart filenames:

- `response_delay_by_phase_strategy.png`
- `speaking_time_by_phase_strategy.png`
- `idea_fluency_by_phase_strategy.png`
- `elaboration_units_by_phase_strategy.png`
- `elaboration_units_per_idea_by_phase_strategy.png`
- `consecutive_topic_turns_by_phase_strategy.png`
- `ues_sf_total.png`
- `open_feedback_theme_counts.png`

Charts are grouped by `phase / strategy` when those columns are present.

## Speaker Labels

By default, these speaker labels are treated as participants:

```text
P1, P2, Participant, participant, Human
```

These labels are treated as robot/facilitator:

```text
Robot, Pepper, Facilitator, robot, pepper
```

If your transcript uses different labels, pass them explicitly:

```powershell
--participant-speakers P1 P2 A B `
--robot-speakers Pepper Robot
```

## Recommended Workflow

1. Generate the templates.
2. Fill `transcript.csv` and `intervention_log.csv`.
3. Run the script once with only transcript and intervention data.
4. Inspect `elicitation_windows.csv` to make sure each window starts and ends
   where expected.
5. Fill manual coding using the confirmed `window_id` values.
6. Add UES-SF and open-feedback data.
7. Run the full command.
8. Use the grouped CSV summaries and PNG charts in the report.

## Common Problems

- Missing required columns: compare your CSV with the generated templates.
- Strange speaking-time values: check that transcript and intervention times use
  the same timeline.
- Zero response delay everywhere: check whether participant speech starts at the
  exact same timestamp as Pepper's prompt end, or whether timestamps are too
  coarse.
- Windows running too long: add `window_end_time` for phase transitions or
  unusual session endings.
- UES-SF scoring error: make sure every item column is present and every response
  is a number from 1 to 5.

## Measurement Notes

Automated metrics:

- response delay
- participant speaking time until the next elicitation strategy
- participant turn count
- participant word count

Manual metrics:

- idea fluency
- elaboration units
- elaborated contribution count
- consecutive turns on the same subject
- open-feedback themes

The manual metrics are intentionally manual because idea boundaries,
elaboration quality, and same-topic continuity require human judgment for this
study.
