import json

with open('D:/__CoChem/__agentic/v4.1.2/audit_payload_ch02_quarantine_vm.md.json', 'r') as f:
    tasks = json.load(f)

for task in tasks:
    # Remove pytest.skip
    if 'pytest.skip' in task['instructions']:
        task['instructions'] = task['instructions'].replace('call pytest.skip only when that binary or daemon is absent on the runner', 'fail the test if that binary or daemon is absent on the runner (no pytest.skip allowed)')
    if 'pytest.skip' in task['delimited_protocol']:
        task['delimited_protocol'] = task['delimited_protocol'].replace('call pytest.skip only when that binary or daemon is absent on the runner', 'fail the test if that binary or daemon is absent on the runner (no pytest.skip allowed)')
    
    # Fix MC-QVM-10
    if task['task_id'] == 'MC-QVM-10':
        task['instructions'] = task['instructions'].replace('the SRS example tag is a leftover of the removed canary and must not pull scientific libraries', 'the image must include physical chemistry libraries (mendeleev, ase) to support the physics canary')
        task['delimited_protocol'] = task['delimited_protocol'].replace('the SRS example tag is a leftover of the removed canary and must not pull scientific libraries', 'the image must include physical chemistry libraries (mendeleev, ase) to support the physics canary')

# Add MC-QVM-05A (Physics Tests)
new_test_task = {
    'schema_version': '4.1.1-wbs-node/1',
    'task_id': 'MC-QVM-05A',
    'title': 'Author Physics Canary and Authentic EMT Simulation Tests',
    'domain': 'quarantine_vm',
    'target_file': 'tests/test_ch02_quarantine_vm.py',
    'chunk_start': 251,
    'chunk_end': 300,
    'line_delta': 50,
    'instructions': 'Append after the previous chunk. Write tests for Section 9 test obligations: assert that mendeleev.element(\'C\').mass matches ~12.011 without hardcoded constants, and verify ASE EMT calculator produces valid non-zero potential energy via verify_physics_canary(). Do NOT mock the physical calculator.',
    'dependencies': ['MC-QVM-05'],
    'verification_command': 'pytest tests/test_ch02_quarantine_vm.py -k "physics_canary"',
    'delimited_protocol': '<<<FILE: tests/test_ch02_quarantine_vm.py>>>\n# [MC-QVM-05A] TESTS:physics_canary\n# SPEC: Append after the previous chunk. Write tests for Section 9 test obligations: assert that\n# SPEC: mendeleev.element(\'C\').mass matches ~12.011 without hardcoded constants, and verify ASE EMT\n# SPEC: calculator produces valid non-zero potential energy via verify_physics_canary(). Do NOT mock\n# SPEC: the physical calculator.\n# tests/test_ch02_quarantine_vm.py:251 [MC-QVM-05A]\n<<<END FILE>>>',
    'rule_18_compliance': {'w1_line_bounds_pass': True, 'w2_diff_ratio_pass': True, 'w3_delimited_syntax_pass': True, 'w5_whole_file_ban_pass': True},
    'srs_trace': {'document_id': 'SRS-412-02', 'items': ['SRS-412-02-FR-005', 'SRS-412-02-FR-006']}
}

# Add MC-QVM-20 (Physics Canary impl)
new_impl_task = {
    'schema_version': '4.1.1-wbs-node/1',
    'task_id': 'MC-QVM-20',
    'title': 'Implement Authentic Physics Verification Canary',
    'domain': 'quarantine_vm',
    'target_file': 'src/cochem/quarantine/canary.py',
    'chunk_start': 1,
    'chunk_end': 50,
    'line_delta': 50,
    'instructions': 'Create the file. Implement verify_physics_canary() -> dict[str, float] exactly as specified in SRS Section 6. Must import Atoms and EMT from ase, and mendeleev. Return the dictionary with C_mass, O_mass, and CO_potential_energy_eV from the EMT evaluation.',
    'dependencies': [],
    'verification_command': 'pytest tests/test_ch02_quarantine_vm.py -k "physics_canary"',
    'delimited_protocol': '<<<FILE: src/cochem/quarantine/canary.py>>>\n# [MC-QVM-20] SRS-412-02-FR-005/FR-006\n# SPEC: Create the file. Implement verify_physics_canary() -> dict[str, float] exactly as specified in\n# SPEC: SRS Section 6. Must import Atoms and EMT from ase, and mendeleev. Return the dictionary with\n# SPEC: C_mass, O_mass, and CO_potential_energy_eV from the EMT evaluation.\n# src/cochem/quarantine/canary.py:7 [MC-QVM-20]\n<<<END FILE>>>',
    'rule_18_compliance': {'w1_line_bounds_pass': True, 'w2_diff_ratio_pass': True, 'w3_delimited_syntax_pass': True, 'w5_whole_file_ban_pass': True},
    'srs_trace': {'document_id': 'SRS-412-02', 'items': ['SRS-412-02-FR-005', 'SRS-412-02-FR-006']}
}

tasks.insert(5, new_test_task)
tasks.append(new_impl_task)

with open('D:/__CoChem/__agentic/v4.1.2/audit_payload_ch02_quarantine_vm_fixed.md.json', 'w') as f:
    json.dump(tasks, f, indent=2)

print('Success')
