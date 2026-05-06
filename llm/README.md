# Pepper Elicitation Infrastructure - Implementation Summary

### 1. Test Scenarios
All test scenarios save logs to `data/logs.csv`:

1. **test_scenario_budget.json** - Budget constraint focus (6 turns, divergence→convergence)
   - Elicitation: constraint_reframing
   - Tests robot's ability to reframe expensive ideas into low-cost alternatives

2. **test_scenario_abstract.json** - Vague-to-concrete progression (6 turns)
   - Elicitation: elaboration_evidence
   - Tests moving from abstract concepts to testable details

3. **test_scenario_perspective.json** - Multi-stakeholder perspectives (6 turns)
   - Elicitation: perspective_shift
   - Tests considering different student types (residential, commuter, international)

4. **test_scenario_wellbeing.json** - Wellbeing theme with realistic constraints (6 turns)
   - Elicitation: constraint_reframing
   - Tests peer-mentor workshops under resource constraints

5. **test_scenario_realistic.json** - Mixed realistic conversation (8 turns, varied momentum)
   - Elicitation: perspective_shift
   - Tests handling rambling, topic-switching, and focus recovery

### 2. Execution Modes

#### --request (One-turn)
```bash
python infra/lmstudio_minimal_bridge.py --request infra/test_request.json
# Output: JSON with fallback/lmstudio source, reply, prompt_id, strategy, phase
```

#### --simulate (Multi-turn scripted)
```bash
python infra/lmstudio_minimal_bridge.py --simulate infra/test_scenario_X.json
# Output: Formatted console conversation with [P1]/[P2]/[Robot] turns
# Logs all turns automatically
```

#### --intervene (Experiment-controlled intervention)
```bash
python infra/lmstudio_minimal_bridge.py --intervene
# asks for group_id and theme_id in terminal
# participant turns are buffered until you type ROBOT/INTERVENE/NOW
# CHANGE switches phase
# every 4th robot reply uses the next unused counterbalanced strategy for that group/theme/phase
# all other replies are context-only (no prompt shaping)
# --initiative proactive/reactive sets the behaviour of the robot
# PROACTIVE makes the robot be proactive, REACTIVE makes the robot be reactive 
```

#### --intervene with Pepper speech output
```bash
python infra/lmstudio_minimal_bridge.py --intervene --initiative reactive --pepper --pepper-ip 192.168.1.35 --pepper-port 9559
```

This keeps the same intervention logic as above:

- reactive mode still triggers when the participant says Pepper or robot
- proactive mode still triggers after silence is detected
- CHANGE still switches between divergence and convergence
- the only difference is that Pepper speaks each LLM reply

If Python 3 does not have NAOqi installed, the bridge falls back to the Python 2.7 helper in `pepper/tts.py` so Pepper can still speak the response.

#### --live (simple back-and-forth conversation)
```bash
python infra/lmstudio_minimal_bridge.py --live
python infra/lmstudio_minimal_bridge.py --live --pepper
```

Use this when you want a straightforward participant/robot dialogue without the intervention schedule.

#### --live with Deepgram microphone input
```bash
python infra/lmstudio_minimal_bridge.py --live --deepgram-live --deepgram-api-key YOUR_API_KEY
```

This records a short microphone segment each turn, transcribes it with Deepgram, and sends the recognized speech to LM Studio.

If you also want Pepper to speak the robot output:
```bash
python infra/lmstudio_minimal_bridge.py --live --deepgram-live --pepper --deepgram-api-key YOUR_API_KEY
```

#### --deepgram-audio (speech recognition to LM Studio)
```bash
python infra/lmstudio_minimal_bridge.py --deepgram-audio path/to/audio.wav --deepgram-api-key YOUR_API_KEY
```

This sends the audio file to Deepgram for transcription, then forwards the recognized text directly to LM Studio as a participant utterance. The bridge prints the transcript and the LM Studio response.

If you want the Deepgram request to use a specific region or endpoint, add:
```bash
python infra/lmstudio_minimal_bridge.py --deepgram-audio path/to/audio.wav --deepgram-api-key YOUR_API_KEY --deepgram-endpoint https://api.eu.deepgram.com/v1/listen
```

## Pepper integration notes

- The LM Studio bridge runs in Python 3.
- Pepper NAOqi TTS is available through the Python 2.7 helper at `pepper/tts.py`.
- The bridge can speak to Pepper directly if NAOqi is installed in the Python 3 environment, or it can call the Python 2.7 relay automatically.
- For now, Pepper input in live mode is console-based unless you add a separate ASR relay.