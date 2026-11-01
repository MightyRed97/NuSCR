# 5 Simulation

Scenarios to replay on the virtual system. Each file is a stimulus: a `cycle` column, then one column per input. A value applies from its cycle on, an empty cell keeps the input's value, lines starting with `#` are comments, and the run lasts until the last cycle listed. Every input starts at its nominal value. In a four-channel run a column is `BP_A.f_PZR_P` (one channel), `BP.f_PZR_P` or a bare `f_PZR_P` (all four BPs).

| File | What happens |
|---|---|
| `scenarios/pzr_pressure_excursion.csv` | Pressurizer pressure excursion: pre-trip at 2360 psia, trip at 2400 psia, held inside the hysteresis band (2380), reset at 2300. |
| `scenarios/cooldown_manual_reset.csv` | Controlled depressurisation with manual setpoint resets after each pressure step, then operating bypass below the 400 psia permissive. The trip never actuates. |
| `scenarios/loss_of_flow_4ch.csv` | Loss of coolant flow in loop 1 seen by all four channels: every BP trips and CP_A initiates a reactor trip. |

Every scenario starts like a restart at power: on cycle 1 the operator presses the high log power bypass button, because that trip is tripped at power by design.

## Replay a scenario

| Where | How |
|---|---|
| In the browser | Open `START_HERE.html`, Virtual system, choose the scenario. |
| C simulator | `4_code\build\rps_sim.exe run SYSTEM 5_simulation\scenarios\loss_of_flow_4ch.csv` |
| Requirements model, one BP | `python -B tools/nuscr_sim.py 5_simulation/scenarios/pzr_pressure_excursion.csv --watch f_PZR_P,th_PZR_P_HI_Pretrip,th_PZR_P_HI_Trip --changes-only` |
| Requirements model, four channels | `python -B tools/nuscr_sim.py 5_simulation/scenarios/loss_of_flow_4ch.csv --system --watch BP_A.f_SG1_F,BP_A.th_SG1_F_LO_Trip,CP_A.f_RT_Init` (in `--system` mode watched names are `INSTANCE.var`) |
| OpenModelica | `RPS.Scenarios` in `3_case_tool/openmodelica/RPS.mo`. |
| SCADE Suite | `3_case_tool/scade/scenarios/*.sss`. |

To write a new scenario, copy one of these files. The verification scripts replay longer generated runs (boundary values, random inputs); see `tools/vectors.py`.
