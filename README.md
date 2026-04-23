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
```