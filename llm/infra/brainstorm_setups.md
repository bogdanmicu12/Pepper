# Pepper TU Delft Brainstorm Setups

Topic for both conditions:

> How might TU Delft better support student wellbeing and academic engagement?

Both setups use Pepper as a socially confident, proactive, competent collaborator. The intended experimental difference is the collaboration method:

- Setup 1 is a live collaborative robot that listens, responds, builds on ideas, and joins after silence or struggle.
- Setup 2 is a prepared solution robot that intervenes at fixed moments with authored ideas and laptop-screen infographics.

The commands below assume:

- Project folder: `C:\Users\Musy-\Pepper`
- Pepper IP: use `auto` for direct Ethernet/link-local discovery, or a fixed robot IP if you know it
- Focusrite input device: `15`
- Focusrite sample rate: `48000`
- Two microphones on Focusrite channels 1 and 2
- Deepgram key stored in `%DEEPGRAM_API_KEY%`
- LM Studio is running when setup 1 uses the local model

Change the IP or audio device number if your machine lists different values.

For direct Ethernet, Pepper's `169.254.x.x` address can change. The study commands use:

```cmd
--pepper-ip auto
```

With this setting, the runner checks the configured IP and then scans `169.254.x.x:9559` for NAOqi if needed.

## Shared Setup

Open CMD:

```cmd
cd /d C:\Users\Musy-\Pepper
```

Set your Deepgram key:

```cmd
set "DEEPGRAM_API_KEY=YOUR_DEEPGRAM_KEY"
```

For setup 1, start LM Studio and load the local chat model. The current recommended quick model is:

```text
qwen/qwen3-8b
```

If LM Studio lists a different exact model name, use that exact name with `--local-model`.

The commands use these voice settings because they are clearer and less rushed:

```cmd
--pepper-language English --tts-volume 1.0 --tts-speed 105 --tts-pitch 1.0 --tts-pause-ms 320
```

The normal study commands use these Focusrite settings for two separate speaker channels:

```cmd
--deepgram-live --deepgram-api-key %DEEPGRAM_API_KEY% --audio-input-device 15 --audio-input-channels 2 --audio-sample-rate 48000 --audio-channel-names "Participant 1,Participant 2" --audio-channel-min-peak 250 --audio-channel-relative-peak 0.25 --audio-channel-relative-rms 0.20
```

The quick real-Pepper commands also include a fallback. They prefer a device whose name contains `Focusrite`, then try device `15`, and if that is not available they use the laptop/default microphone as one channel:

```cmd
--audio-prefer-device-name Focusrite --audio-input-device 15 --audio-fallback-to-default-input --audio-fallback-channels 1 --audio-fallback-sample-rate 0
```

During live runs, CMD should show transcripts like:

```text
[Deepgram] Participant 1: ...
[Deepgram] Participant 2: ...
```

If the fallback activates, the transcript appears as a single combined Deepgram transcript instead of separate Participant 1/2 channels.

The channel filters reduce duplicate transcripts when one Focusrite channel picks up a quieter copy of the other microphone:

```cmd
--audio-channel-min-peak 250 --audio-channel-relative-peak 0.25 --audio-channel-relative-rms 0.20
```

Setup 1 is tuned as an assertive collaborator, but now leaves a little more room for people to finish their sentence. Pepper may briefly overlap with participants, but it should no longer disappear or wait too long. The speech-start threshold is higher so room noise is less likely to count as participant speech. Pepper waits a short beat, then joins:

```cmd
--audio-speech-peak-threshold 350 --audio-endpoint-silence-seconds 1.3 --pre-speech-quiet-seconds 0.45 --pre-speech-max-wait-seconds 1.5 --pre-speech-max-wait-action speak --pre-speech-peak-threshold 1200
```

Setup 1 is also tuned to be proactive. After participant input, Pepper can join after about `1.2` seconds during active discussion, or about `4` seconds during a general pause. If the speech gate stays active from room noise for more than `1.2` seconds, Pepper treats it as noise and joins anyway. The LLM prompt only receives a small recent-context window, and Pepper is instructed to name or paraphrase the newest participant idea before adding its own mechanism, first step, metric, or stakeholder.

The required setup 1 structure announcements bypass that defer rule. Pepper will still say that divergence is starting at the beginning, that convergence is starting at the phase change, and that final ideas are being requested at the end, even if participants are still talking.

Transcript/event CSV files are written to `logs\...csv`.

## Participant Script: Setup 1

Read this before the collaborative setup:

```text
In this brainstorm, Pepper will act as a collaborative robot partner. The topic is: how might TU Delft better support student wellbeing and academic engagement?

You can discuss freely with each other. Pepper is not only waiting to be called on; it is an active collaborator and may enter the conversation when it notices a pause, when the group seems stuck, or when it has a useful idea to add. Because Pepper is working from live microphone input, there may occasionally be a brief overlap where Pepper starts just as someone continues speaking. If that happens, simply finish your thought naturally; it is part of working with an active robot collaborator.

If you want Pepper to respond directly, say "Pepper" or "robot," ask your question, finish your sentence, and leave a brief pause. Pepper usually waits for a short silence before speaking, but it may also take initiative when it thinks the discussion could use a next step.

Please try to speak one at a time as much as possible. That helps Pepper follow the discussion clearly. You do not need to stop your own idea just because Pepper begins speaking; finish your sentence, then continue from Pepper's contribution if it is useful. If you feel stuck, you can keep thinking aloud; Pepper may step in after a pause. If you want help immediately, you can call Pepper directly.

The brainstorm has two parts. First, you will generate many ideas. Later, you will converge and try to shape the strongest ideas into one final proposal. At the end, each of you will be asked for your final idea. If you have nothing else to add, you can say "I have nothing to add" or "we are done."
```

## Setup 1: Quick Laptop Mic Test, No Pepper

Use this when Pepper is not available and you want to confirm Deepgram hears your laptop microphone. Do not add `--audio-input-device 15`; it will use the default laptop mic.

```cmd
python .\llm\infra\pepper_brainstorm.py --session dynamic --mock-pepper --local-model qwen/qwen3-8b --deepgram-live --deepgram-api-key %DEEPGRAM_API_KEY% --audio-input-channels 1 --audio-sample-rate 0 --audio-speech-peak-threshold 350 --audio-endpoint-silence-seconds 1.3 --divergence-seconds 45 --convergence-seconds 45 --final-collection-seconds 20 --final-silence-seconds 8 --dynamic-silence-seconds 4 --dynamic-active-quiet-seconds 1.2 --dynamic-active-quiet-turns 1 --dynamic-active-quiet-words 8 --dynamic-speech-gate-max-block-seconds 1.2 --transcript-log logs\setup1_laptop_quick_transcript.csv --session-id S01 --group-id G01 --conversation-id setup1_laptop_quick
```

## Setup 1: Quick Real Pepper Test

Use this for a short live test with Pepper voice. It tries the Focusrite two-mic input first; if Focusrite is unavailable, it falls back to the laptop/default microphone.

```cmd
python .\llm\infra\pepper_brainstorm.py --session dynamic --pepper --pepper-optional --pepper-ip auto --pepper-language English --local-model qwen/qwen3-8b --deepgram-live --deepgram-api-key %DEEPGRAM_API_KEY% --audio-prefer-device-name Focusrite --audio-input-device 15 --audio-fallback-to-default-input --audio-fallback-channels 1 --audio-fallback-sample-rate 0 --audio-input-channels 2 --audio-sample-rate 48000 --audio-channel-names "Participant 1,Participant 2" --audio-channel-min-peak 250 --audio-channel-relative-peak 0.25 --audio-channel-relative-rms 0.20 --audio-speech-peak-threshold 350 --audio-endpoint-silence-seconds 1.3 --tts-volume 1.0 --tts-speed 105 --tts-pitch 1.0 --tts-pause-ms 320 --divergence-seconds 45 --convergence-seconds 45 --final-collection-seconds 20 --final-silence-seconds 8 --dynamic-silence-seconds 4 --dynamic-active-quiet-seconds 1.2 --dynamic-active-quiet-turns 1 --dynamic-active-quiet-words 8 --dynamic-speech-gate-max-block-seconds 1.2 --pre-speech-quiet-seconds 0.45 --pre-speech-max-wait-seconds 1.5 --pre-speech-max-wait-action speak --pre-speech-peak-threshold 1200 --transcript-log logs\setup1_quick_transcript.csv --session-id S01 --group-id G01 --conversation-id setup1_quick
```

## Setup 1: Normal Real Session

This is the 20-minute collaborative version: 10 minutes divergence, 10 minutes convergence, then final plans.

```cmd
python .\llm\infra\pepper_brainstorm.py --session dynamic --pepper --pepper-optional --pepper-ip auto --pepper-language English --local-model qwen/qwen3-8b --deepgram-live --deepgram-api-key %DEEPGRAM_API_KEY% --audio-input-device 15 --audio-input-channels 2 --audio-sample-rate 48000 --audio-channel-names "Participant 1,Participant 2" --audio-channel-min-peak 250 --audio-channel-relative-peak 0.25 --audio-channel-relative-rms 0.20 --audio-speech-peak-threshold 350 --audio-endpoint-silence-seconds 1.3 --tts-volume 1.0 --tts-speed 105 --tts-pitch 1.0 --tts-pause-ms 320 --divergence-seconds 600 --convergence-seconds 600 --final-collection-seconds 120 --final-silence-seconds 8 --dynamic-silence-seconds 4 --dynamic-active-quiet-seconds 1.2 --dynamic-active-quiet-turns 1 --dynamic-active-quiet-words 8 --dynamic-speech-gate-max-block-seconds 1.2 --pre-speech-quiet-seconds 0.45 --pre-speech-max-wait-seconds 1.5 --pre-speech-max-wait-action speak --pre-speech-peak-threshold 1200 --transcript-log logs\setup1_full_transcript.csv --session-id S01 --group-id G01 --conversation-id setup1_full
```

Setup 1 does not use tablet or laptop infographics. Pepper is supposed to stay concise during live collaboration. The final synthesis during convergence can be longer and more plan-like.

## Participant Script: Setup 2

Read this before the prepared intervention setup:

```text
In this brainstorm, Pepper will work alongside you as a robot collaborator with prepared solution ideas. The topic is: how might TU Delft better support student wellbeing and academic engagement?

You can discuss freely with each other. Pepper will not respond continuously in this setup. Instead, Pepper will step in at specific moments during the session with prepared ideas, a synthesis, and a final proposal.

When Pepper is about to intervene, it may ask for your attention and give you a moment to finish your current thought. Please finish your sentence, then leave a brief pause so Pepper can speak without interrupting.

Please try to speak one at a time as much as possible. That helps the microphones capture both speakers clearly. You do not need to call Pepper during this setup; Pepper will enter at the planned moments.

At the end, Pepper will ask each of you to share your final plan. If you have nothing else to add, you can say "I have nothing to add" or "we are done." Pepper will then present its final proposal.

Setup 2 is tuned to move on after a short finished-sentence pause: after an attention cue it waits for about `0.8` seconds of quiet, with an `8` second safety cap if the audio gate gets stuck. During final-plan collection it synthesizes after about `4` seconds of quiet, or about `6` seconds if the audio gate still thinks the room is busy.
```

## Setup 2: Quick Laptop Display Test, No Pepper

Use this when Pepper is not available. It shows the hardcoded infographic images on this laptop screen and prints/saves transcripts.

```cmd
python .\llm\infra\pepper_brainstorm.py --session pregenerated --pregenerated-static --mock-pepper --laptop-display --deepgram-live --deepgram-api-key %DEEPGRAM_API_KEY% --audio-input-channels 1 --audio-sample-rate 0 --total-seconds 60 --intervention-seconds 15,35,55 --final-collection-seconds 20 --final-silence-seconds 4 --final-empty-silence-seconds 4 --setup2-final-silence-seconds 4 --setup2-final-empty-silence-seconds 4 --setup2-final-gate-grace-seconds 2 --transcript-log logs\setup2_laptop_quick_transcript.csv --session-id S01 --group-id G01 --conversation-id setup2_laptop_quick
```

## Setup 2: Quick Real Pepper Test

Use this for a short static intervention test. It uses Pepper voice, opens the hardcoded images on the laptop screen, and falls back to the laptop/default microphone if Focusrite is unavailable.

```cmd
python .\llm\infra\pepper_brainstorm.py --session pregenerated --pregenerated-static --pepper --pepper-optional --laptop-display --pepper-ip auto --pepper-language English --deepgram-live --deepgram-api-key %DEEPGRAM_API_KEY% --audio-prefer-device-name Focusrite --audio-input-device 15 --audio-fallback-to-default-input --audio-fallback-channels 1 --audio-fallback-sample-rate 0 --audio-input-channels 2 --audio-sample-rate 48000 --audio-channel-names "Participant 1,Participant 2" --audio-channel-min-peak 250 --audio-channel-relative-peak 0.25 --audio-channel-relative-rms 0.20 --tts-volume 1.0 --tts-speed 105 --tts-pitch 1.0 --tts-pause-ms 320 --total-seconds 60 --intervention-seconds 15,35,55 --final-collection-seconds 20 --final-silence-seconds 4 --final-empty-silence-seconds 4 --setup2-post-cue-grace-seconds 0.4 --setup2-post-cue-quiet-seconds 0.8 --setup2-post-cue-max-wait-seconds 8 --setup2-listen-pause-seconds 0.4 --setup2-final-silence-seconds 4 --setup2-final-empty-silence-seconds 4 --setup2-final-gate-grace-seconds 2 --transcript-log logs\setup2_quick_transcript.csv --session-id S01 --group-id G01 --conversation-id setup2_quick
```

## Setup 2: Normal Real Session

This is the 20-minute prepared intervention version. Pepper now lets the participants start first, then intervenes around 2 minutes, 10 minutes, and 20 minutes. At the last intervention it asks for final plans and presents its final proposal.

```cmd
python .\llm\infra\pepper_brainstorm.py --session pregenerated --pregenerated-static --pepper --pepper-optional --laptop-display --pepper-ip auto --pepper-language English --deepgram-live --deepgram-api-key %DEEPGRAM_API_KEY% --audio-input-device 15 --audio-input-channels 2 --audio-sample-rate 48000 --audio-channel-names "Participant 1,Participant 2" --audio-channel-min-peak 250 --audio-channel-relative-peak 0.25 --audio-channel-relative-rms 0.20 --tts-volume 1.0 --tts-speed 105 --tts-pitch 1.0 --tts-pause-ms 320 --total-seconds 1200 --intervention-seconds 120,600,1200 --final-collection-seconds 120 --final-silence-seconds 4 --final-empty-silence-seconds 4 --setup2-post-cue-grace-seconds 0.4 --setup2-post-cue-quiet-seconds 0.8 --setup2-post-cue-max-wait-seconds 8 --setup2-listen-pause-seconds 0.4 --setup2-final-silence-seconds 4 --setup2-final-empty-silence-seconds 4 --setup2-final-gate-grace-seconds 2 --transcript-log logs\setup2_full_transcript.csv --session-id S01 --group-id G01 --conversation-id setup2_full
```

Setup 2 uses static authored content from:

```text
llm\infra\pregenerated_static_content.json
```

The hardcoded images are stored in:

```text
llm\tablet\assets\setup2_intervention_1.png
llm\tablet\assets\setup2_intervention_2.png
llm\tablet\assets\setup2_final.png
```

The current intervention order is:

1. `setup2_intervention_1.png`: 30 Prepared Solutions
2. `setup2_intervention_2.png`: Delft Wellbeing Loop
3. `setup2_final.png`: Integrated Support Loop

Use `--laptop-display` for the study unless you specifically need Pepper's tablet. Laptop display opens a local file wrapper, so it does not depend on Pepper reaching `127.0.0.1` or a tablet HTTP server.

## Original Minimal Commands

These are useful for debugging without microphone, Pepper, or laptop display.

Setup 1 minimal:

```cmd
python .\llm\infra\pepper_brainstorm.py --session dynamic --mock-pepper --local-model qwen/qwen3-8b
```

Setup 2 minimal static:

```cmd
python .\llm\infra\pepper_brainstorm.py --session pregenerated --pregenerated-static --mock-pepper
```

Setup 2 local generated text, no OpenAI:

```cmd
set "OPENAI_API_KEY="
python .\llm\infra\pepper_brainstorm.py --session pregenerated --mock-pepper --local-model qwen/qwen3-8b --pregenerated-text-provider local
```

## Microphone Checks

List audio devices:

```cmd
python -c "import sounddevice as sd; print(sd.query_devices())"
```

Test laptop microphone once:

```cmd
python .\llm\infra\pepper_brainstorm.py --session dynamic --mock-pepper --deepgram-live --deepgram-test-once --deepgram-api-key %DEEPGRAM_API_KEY% --audio-input-channels 1 --audio-sample-rate 0
```

Test Focusrite two-channel transcription once:

```cmd
python .\llm\infra\pepper_brainstorm.py --session dynamic --mock-pepper --deepgram-live --deepgram-test-once --deepgram-api-key %DEEPGRAM_API_KEY% --audio-input-device 15 --audio-input-channels 2 --audio-sample-rate 48000 --audio-channel-names "Participant 1,Participant 2" --audio-channel-min-peak 250 --audio-channel-relative-peak 0.25 --audio-channel-relative-rms 0.20
```

If you see this error:

```text
Invalid sample rate [PaErrorCode -9997]
```

Use the Focusrite default sample rate:

```cmd
--audio-sample-rate 48000
```

or let the program ask the device for its default:

```cmd
--audio-sample-rate 0
```

## Adjusting Timing

If Pepper still talks over people too often, increase one or more of these:

```cmd
--pre-speech-quiet-seconds 1.0 --dynamic-response-hold-seconds 0.8 --audio-endpoint-silence-seconds 1.5
```

If Pepper feels too slow to join in, lower one or more of these:

```cmd
--dynamic-silence-seconds 5 --dynamic-active-quiet-seconds 2 --dynamic-direct-response-quiet-seconds 0.2
```

No system can perfectly guarantee that Pepper never talks over someone, because live speech detection and Deepgram transcripts arrive with some delay. The safest study instruction is: speak one at a time, finish the sentence, then leave a brief pause when Pepper is expected to answer.

## Useful Spoken Phrases

Participants can stop or speed up final collection naturally:

```text
we are done
I have nothing to add
no more ideas
stop the session
```

Setup 1 direct-address phrases:

```text
Pepper, what do you think?
Robot, can you build on that?
Pepper, give us a new idea.
```

The transcript normalizer also treats common ASR variants such as `Peper`, `Pappa`, and similar forms as Pepper-directed speech.

## Troubleshooting

If Pepper speaks Dutch, add or keep:

```cmd
--pepper-language English
```

If setup 2 images do not appear on Pepper's tablet, use laptop display:

```cmd
--laptop-display
```

If a browser tab shows `ERR_EMPTY_RESPONSE` on `127.0.0.1`, close that tab and rerun setup 2 with `--laptop-display`. The current laptop-display path opens hardcoded local PNGs instead of relying on a localhost server.

If Pepper cannot connect:

```text
Cannot connect to tcp://ROBOT_IP:9559
```

Check that the robot IP is correct, Pepper and the laptop are on the same network, and Choregraphe/NAOqi access works for that robot. With `--pepper-optional`, the session continues in mock mode if Pepper TTS fails.

