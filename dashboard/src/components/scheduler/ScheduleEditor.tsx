import React, { useMemo, useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { createThemedStyles } from '../../theme/ThemeContext';
import { colors } from '../../theme/colors';
import { useConnectionStore } from '../../stores/connectionStore';
import { useUIStore } from '../../stores/uiStore';
import { useTriggerPreview } from '../../hooks/useTriggerPreview';
import type { PinSession } from '../../hooks/usePinSession';
import { checkScript, createSchedule, ScheduleApiError, updateSchedule } from '../../services/schedules';
import { buildInput, buildTrigger, defaultForm, fieldMessages, formFromDetail, type ScheduleForm } from '../../utils/scheduleForm';
import { describeScheduleError, machineName, schedulerStateColor, schedulerStateLabel } from '../../utils/schedules';
import type { FieldError, ScheduleDetail } from '../../types/schedules';
import { TriggerEditor } from './TriggerEditor';
import { ActionButton, Chip, FormField, Input } from './ui';

const TRIGGER_KEYS = ['trigger', 'cron', 'at', 'every_seconds', 'start_at', 'time', 'days_of_week', 'day_of_month', 'event', 'after_schedule_id', 'on'];
const KNOWN_KEYS = [
  ...TRIGGER_KEYS,
  'name',
  'machine_id',
  'script',
  'args',
  'enabled',
  'controls',
  'timeout_s',
  'retries',
  'retry_delay_s',
  'no_overlap',
  'active_from',
  'active_until',
  'run_if_missed',
  'fail_threshold',
];

const useStyles = createThemedStyles((c) => ({
  wrap: { gap: 16 },
  section: { backgroundColor: c.surface, borderRadius: 12, padding: 14, gap: 12 },
  sectionTitle: { color: c.textMuted, fontSize: 12, fontWeight: '800', textTransform: 'uppercase', letterSpacing: 0.6 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  machineChip: { borderRadius: 10, borderWidth: 1, borderColor: c.border, backgroundColor: c.background, paddingHorizontal: 12, paddingVertical: 8, gap: 2 },
  machineChipActive: { borderColor: colors.accent, backgroundColor: colors.blockedDim },
  machineName: { color: c.textPrimary, fontSize: 13, fontWeight: '800' },
  machineState: { fontSize: 11, fontWeight: '700' },
  row: { flexDirection: 'row', gap: 10, alignItems: 'center' },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 12 },
  argRow: { flexDirection: 'row', gap: 8, alignItems: 'center' },
  checkNote: { fontSize: 12, fontWeight: '700' },
  banner: { borderRadius: 8, padding: 10, backgroundColor: colors.badDim },
  bannerText: { color: colors.bad, fontSize: 13, fontWeight: '700' },
  actions: { flexDirection: 'row', gap: 10, flexWrap: 'wrap' },
}));

type CheckState =
  | { kind: 'idle' }
  | { kind: 'checking' }
  | { kind: 'ok'; text: string }
  | { kind: 'missing'; text: string }
  | { kind: 'error'; text: string };

interface Props {
  initial?: ScheduleDetail;
  pin: PinSession;
  onSaved: (d: ScheduleDetail) => void;
  onCancel: () => void;
}

export function ScheduleEditor({ initial, pin, onSaved, onCancel }: Props) {
  const styles = useStyles();
  const machines = useConnectionStore((s) => s.machines);
  const schedulesById = useConnectionStore((s) => s.schedules);
  const showSuccess = useUIStore((s) => s.showSuccess);
  const showError = useUIStore((s) => s.showError);

  const schedulerMachines = useMemo(
    () =>
      Object.values(machines)
        .filter((m) => m.machine_scheduler || m.machine_id === initial?.machine_id)
        .sort((a, b) => a.machine_id - b.machine_id),
    [machines, initial?.machine_id]
  );

  const [form, setForm] = useState<ScheduleForm>(() =>
    initial ? formFromDetail(initial) : defaultForm(schedulerMachines.length === 1 ? schedulerMachines[0].machine_id : null)
  );
  const [saving, setSaving] = useState(false);
  const [saveErrors, setSaveErrors] = useState<FieldError[]>([]);
  const [banner, setBanner] = useState<string | null>(null);
  const [check, setCheck] = useState<CheckState>({ kind: 'idle' });
  const [touched, setTouched] = useState(false);

  const trigger = useMemo(() => buildTrigger(form.trigger), [form.trigger]);
  const preview = useTriggerPreview(trigger);
  const effective = initial?.effective;
  const scheduleList = useMemo(() => Object.values(schedulesById).sort((a, b) => a.id - b.id), [schedulesById]);

  const patch = (p: Partial<ScheduleForm>) => {
    setForm((f) => ({ ...f, ...p }));
    setSaveErrors([]);
    setBanner(null);
  };
  const patchControls = (p: Partial<ScheduleForm['controls']>) => patch({ controls: { ...form.controls, ...p } });

  const triggerSaveErrors = saveErrors.filter((e) => fieldMessages([e], TRIGGER_KEYS).length > 0);
  const allTriggerErrors = [...preview.errors, ...triggerSaveErrors.filter((e) => !preview.errors.some((p) => p.message === e.message))];
  const unmatched = saveErrors.filter((e) => fieldMessages([e], KNOWN_KEYS).length === 0);

  const nameErrors = [...(touched && !form.name.trim() ? ['Name is required.'] : []), ...fieldMessages(saveErrors, ['name'])];
  const machineErrors = [...(touched && form.machineId == null ? ['Pick a machine.'] : []), ...fieldMessages(saveErrors, ['machine_id'])];
  const scriptErrors = [...(touched && !form.script.trim() ? ['Script path is required.'] : []), ...fieldMessages(saveErrors, ['script'])];

  const notNumber = (text: string, label: string) => (text.trim() && !Number.isFinite(Number(text.trim())) ? [`${label} must be a number.`] : []);
  const timeoutErrors = [...notNumber(form.controls.timeout, 'Timeout'), ...fieldMessages(saveErrors, ['timeout_s'])];
  const retriesErrors = [...notNumber(form.controls.retries, 'Retries'), ...fieldMessages(saveErrors, ['retries'])];
  const retryDelayErrors = [...notNumber(form.controls.retryDelay, 'Retry delay'), ...fieldMessages(saveErrors, ['retry_delay_s'])];
  const thresholdErrors = [...notNumber(form.failThreshold, 'Failure threshold'), ...fieldMessages(saveErrors, ['fail_threshold'])];

  async function runCheck() {
    if (form.machineId == null || !form.script.trim()) {
      setTouched(true);
      return;
    }
    const machineId = form.machineId;
    setCheck({ kind: 'checking' });
    try {
      const res = await pin.run((p) => checkScript(machineId, form.script.trim(), p), 'Checking the path runs on the machine.');
      if (!res) {
        setCheck({ kind: 'idle' });
        return;
      }
      if (res.error === 'outside_scripts_dir') {
        setCheck({ kind: 'error', text: "Outside the machine's services folder — use a name inside services\ or an absolute path." });
      } else if (res.error === 'unsupported_on_this_os') {
        setCheck({ kind: 'error', text: "Not supported on this machine's OS." });
      } else if (res.error) {
        setCheck({ kind: 'error', text: res.error });
      } else if (res.exists && res.is_file) {
        setCheck({ kind: 'ok', text: res.resolved ? `Found: ${res.resolved}` : 'File exists on the machine.' });
      } else {
        const where = res.resolved ? `: ${res.resolved}` : '';
        setCheck({ kind: 'missing', text: res.exists ? `That path exists but is not a file${where}.` : `File not found on the machine${where}.` });
      }
    } catch (e) {
      setCheck({ kind: 'error', text: `Could not check: ${describeScheduleError(e)}` });
    }
  }

  async function save() {
    setTouched(true);
    const input = buildInput(form);
    const numericProblems = [...timeoutErrors, ...retriesErrors, ...retryDelayErrors, ...thresholdErrors].filter((m) => m.endsWith('must be a number.'));
    if (!form.name.trim() || !form.script.trim() || form.machineId == null || numericProblems.length > 0) return;
    if (!input) {
      setBanner('Complete the trigger first.');
      return;
    }
    setSaving(true);
    setBanner(null);
    try {
      const saved = await pin.run(
        (p) => (initial ? updateSchedule(initial.id, input, p) : createSchedule(input, p)),
        initial ? `Saving changes to #${initial.id}.` : 'Creating this schedule.'
      );
      if (!saved) return;
      showSuccess(initial ? `Schedule #${saved.id} updated` : `Schedule #${saved.id} created`);
      onSaved(saved);
    } catch (e) {
      if (e instanceof ScheduleApiError && e.status === 422) {
        setSaveErrors(e.fieldErrors);
        setBanner(e.fieldErrors.length === 0 ? e.message : 'Fix the highlighted fields.');
      } else {
        const text = describeScheduleError(e);
        setBanner(text);
        showError(text);
      }
    } finally {
      setSaving(false);
    }
  }

  const ctl = form.controls;
  const machineDefaults = form.machineId != null ? machines[form.machineId]?.scheduler_defaults : null;
  const ph = (key: 'timeout_s' | 'retries' | 'retry_delay_s' | 'fail_threshold') => {
    const show = (n: number) => (key === 'timeout_s' && n === 0 ? 'none' : String(n));
    const m = machineDefaults?.[key];
    if (m != null) return `${show(m)} (machine default)`;
    const e = effective?.[key];
    if (e != null && initial?.effective_source?.[key] !== 'schedule') return `default ${show(e)}`;
    return 'config default';
  };

  return (
    <View style={styles.wrap}>
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>What</Text>
        <FormField label="Name" errors={nameErrors}>
          <Input value={form.name} onChangeText={(name) => patch({ name })} placeholder="MySQL nightly start" autoCapitalize="sentences" invalid={nameErrors.length > 0} />
        </FormField>

        <FormField label="Machine" errors={machineErrors}>
          {schedulerMachines.length === 0 ? (
            <Text style={styles.checkNote}>No machine has the scheduler enabled (machine_scheduler).</Text>
          ) : (
            <View style={styles.chips}>
              {schedulerMachines.map((m) => {
                const active = form.machineId === m.machine_id;
                return (
                  <Pressable
                    key={m.machine_id}
                    style={[styles.machineChip, active ? styles.machineChipActive : null]}
                    onPress={() => patch({ machineId: m.machine_id })}
                  >
                    <Text style={styles.machineName}>{machineName(machines, m.machine_id)}</Text>
                    <Text style={[styles.machineState, { color: schedulerStateColor(m.scheduler_state) }]}>{schedulerStateLabel(m.scheduler_state)}</Text>
                  </Pressable>
                );
              })}
            </View>
          )}
        </FormField>

        <FormField label="Script path" hint="Relative paths run from the machine's services folder (deploy\<machine>\services). .py, .ps1, .cmd/.bat (and .sh on Linux) are supported." errors={scriptErrors}>
          <View style={styles.row}>
            <Input
              value={form.script}
              onChangeText={(script) => {
                patch({ script });
                setCheck({ kind: 'idle' });
              }}
              placeholder="log-time-script.py or D:\jobs\task.cmd"
              invalid={scriptErrors.length > 0}
              style={{ flex: 1 }}
            />
            <ActionButton label={check.kind === 'checking' ? 'Checking...' : 'Check'} onPress={runCheck} disabled={check.kind === 'checking'} />
          </View>
          {check.kind === 'ok' ? <Text style={[styles.checkNote, { color: colors.good }]}>{check.text}</Text> : null}
          {check.kind === 'missing' ? <Text style={[styles.checkNote, { color: colors.warn }]}>{check.text} You can still save.</Text> : null}
          {check.kind === 'error' ? <Text style={[styles.checkNote, { color: colors.unknown }]}>{check.text}</Text> : null}
        </FormField>

        <FormField label="Arguments" errors={fieldMessages(saveErrors, ['args'])}>
          {form.args.map((a, i) => (
            <View key={i} style={styles.argRow}>
              <Input
                value={a}
                onChangeText={(v) => patch({ args: form.args.map((x, j) => (j === i ? v : x)) })}
                placeholder={`argument ${i + 1}`}
                style={{ flex: 1 }}
              />
              <ActionButton label="Remove" onPress={() => patch({ args: form.args.filter((_, j) => j !== i) })} />
            </View>
          ))}
          <View style={styles.row}>
            <ActionButton label="Add argument" onPress={() => patch({ args: [...form.args, ''] })} />
          </View>
        </FormField>

        <View style={styles.row}>
          <Chip label={form.enabled ? 'Enabled' : 'Disabled'} active={form.enabled} onPress={() => patch({ enabled: !form.enabled })} />
        </View>
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>When</Text>
        <TriggerEditor
          value={form.trigger}
          onChange={(t) => patch({ trigger: t })}
          schedules={scheduleList}
          selfId={initial?.id}
          errors={allTriggerErrors}
          message={preview.message}
          preview={preview.result}
          previewLoading={preview.loading}
        />
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Run controls</Text>
        <View style={styles.grid}>
          <FormField label="Timeout (s)" hint="Empty or 0 = no timeout" errors={timeoutErrors}>
            <Input value={ctl.timeout} onChangeText={(timeout) => patchControls({ timeout })} keyboardType="numeric" placeholder={ph('timeout_s')} style={{ width: 140 }} />
          </FormField>
          <FormField label="Retries" errors={retriesErrors}>
            <Input value={ctl.retries} onChangeText={(retries) => patchControls({ retries })} keyboardType="numeric" placeholder={ph('retries')} style={{ width: 140 }} />
          </FormField>
          <FormField label="Retry delay (s)" errors={retryDelayErrors}>
            <Input value={ctl.retryDelay} onChangeText={(retryDelay) => patchControls({ retryDelay })} keyboardType="numeric" placeholder={ph('retry_delay_s')} style={{ width: 140 }} />
          </FormField>
          <FormField label="Failure threshold" hint="Consecutive failures before red" errors={thresholdErrors}>
            <Input value={form.failThreshold} onChangeText={(failThreshold) => patch({ failThreshold })} keyboardType="numeric" placeholder={ph('fail_threshold')} style={{ width: 140 }} />
          </FormField>
        </View>
        <View style={styles.grid}>
          <FormField label="Active from (UTC)" hint="YYYY-MM-DD HH:MM, optional" errors={fieldMessages(saveErrors, ['active_from', 'controls'])}>
            <Input value={ctl.activeFrom} onChangeText={(activeFrom) => patchControls({ activeFrom })} placeholder="2026-10-06 00:00" style={{ width: 190 }} />
          </FormField>
          <FormField label="Active until (UTC)" hint="YYYY-MM-DD HH:MM, optional" errors={fieldMessages(saveErrors, ['active_until'])}>
            <Input value={ctl.activeUntil} onChangeText={(activeUntil) => patchControls({ activeUntil })} placeholder="2026-12-31 00:00" style={{ width: 190 }} />
          </FormField>
        </View>
        <View style={styles.chips}>
          <Chip label={ctl.noOverlap ? 'No overlap: on' : 'No overlap: off'} active={ctl.noOverlap} onPress={() => patchControls({ noOverlap: !ctl.noOverlap })} />
        </View>
        <FormField
          label="Run if missed"
          hint={machineDefaults?.run_if_missed != null ? `Machine default: ${machineDefaults.run_if_missed ? 'yes' : 'no'}` : effective && initial?.effective_source?.run_if_missed !== 'schedule' ? `Default: ${effective.run_if_missed ? 'yes' : 'no'}` : undefined}
          errors={fieldMessages(saveErrors, ['run_if_missed'])}
        >
          <View style={styles.chips}>
            <Chip label="Default" active={ctl.runIfMissed === 'default'} onPress={() => patchControls({ runIfMissed: 'default' })} />
            <Chip label="Yes" active={ctl.runIfMissed === 'yes'} onPress={() => patchControls({ runIfMissed: 'yes' })} />
            <Chip label="No" active={ctl.runIfMissed === 'no'} onPress={() => patchControls({ runIfMissed: 'no' })} />
          </View>
        </FormField>
      </View>

      {unmatched.length > 0 ? (
        <View style={styles.banner}>
          {unmatched.map((e, i) => (
            <Text key={i} style={styles.bannerText}>
              {e.field ? `${e.field}: ` : ''}
              {e.message}
            </Text>
          ))}
        </View>
      ) : null}
      {banner ? (
        <View style={styles.banner}>
          <Text style={styles.bannerText}>{banner}</Text>
        </View>
      ) : null}

      <View style={styles.actions}>
        <ActionButton label={saving ? 'Saving...' : initial ? 'Save changes' : 'Create schedule'} tone="primary" onPress={save} disabled={saving} />
        <ActionButton label="Cancel" onPress={onCancel} disabled={saving} />
      </View>
    </View>
  );
}
