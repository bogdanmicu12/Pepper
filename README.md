# Pepper Elicitation Infrastructure - Implementation Summary

### 1. Test Scenarios
All test scenarios save logs to `data/prompt_log.csv`:

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
python infra/lmstudio_minimal_bridge.py --request infra/example_request.json
# Output: JSON with fallback/lmstudio source, reply, prompt_id, strategy, phase
```

#### --simulate (Multi-turn scripted)
```bash
python infra/lmstudio_minimal_bridge.py --simulate infra/example_session.json
# Output: Formatted console conversation with [P1]/[P2]/[Robot] turns
# Logs all turns automatically
```

#### --interactive (Console-driven with CHANGE)
```bash
python infra/lmstudio_minimal_bridge.py --interactive
# Prompts for participant input
# Accepts "CHANGE" to toggle phase (divergence ↔ convergence)
# Logs to data/prompt_log.csv in real-time
# Exit with "quit" or "exit"
```

### Simulation Test (Budget Scenario)
```
# Session S_BUDGET / group G_BUDGET
[P1] We need a comprehensive mentorship program...
[Robot:fallback/P-D-03] If accessibility were the starting point...
[P2] That would be great, but the budget...
[Robot:fallback/P-D-04] How would this idea change for a student...
[P1] Yeah, so maybe something smaller...
[Robot:fallback/P-D-05] If budget were nearly zero...
[P2] Volunteers could work...
[Robot:fallback/P-C-04] Which idea is most robust...
[P1] What if we pair first-year ambassadors...
[Robot:fallback/P-C-05] Which idea has best impact-to-effort...
[P2] Maybe start with just one faculty?
[Robot:fallback/P-C-06] Which concept is easiest to pilot...
```

##  How to Use

### Run Interactive Session with CHANGE Keyword
```bash
cd Pepper
python infra/lmstudio_minimal_bridge.py --interactive

# Type: We have some initial ideas
# Robot responds with facilitation prompt
# Type: CHANGE
# Robot confirms: "--- Switched to phase: convergence ---"
# Type: Which idea should we prioritize?
# Robot responds with convergence-phase prompt
# Type: exit
```

### Simulate a Test Scenario
```bash
# Test budget constraint handling
python infra/lmstudio_minimal_bridge.py --simulate infra/test_scenario_budget.json

# Check logs
Get-Content data/prompt_log.csv | Select-Object -First 5
```

### Run One-Turn Request
```bash
python infra/lmstudio_minimal_bridge.py --request infra/example_request.json
# Output: JSON with reply, strategy, phase, fallback_reason
```