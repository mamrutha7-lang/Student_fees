import { ChangeDetectionStrategy, Component, signal } from '@angular/core';
import { environment } from '../../../../environments/environment';
import { readPickedFile, UNREADABLE_FILE_MESSAGE, UPLOAD_DROPPED_MESSAGE } from '../../../core/picked-file';

interface ImportResult {
  rows: number;
  updated: number;
  unmatched: string[];
  skipped: number;
}

/**
 * The college's role-preference form, loaded onto the students it names. Each
 * matched student's landing then shows their first preference as their career
 * goal and the next two as backups. Main Admin only.
 */
@Component({
  selector: 'app-admin-role-preferences',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="dt-header">
      <div>
        <h1 class="dt-title">Role preferences</h1>
        <p class="dt-sub">Upload the role-preference form's response sheet (.xlsx).</p>
      </div>
    </div>
    <div class="card">
      <button type="button" class="btn primary" [disabled]="busy()" (click)="picker.click()">
        <span class="icon" aria-hidden="true">upload</span>
        {{ busy() ? 'Uploading…' : 'Choose sheet' }}
      </button>
      <input
        #picker
        class="visually-hidden-input"
        type="file"
        accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        (change)="upload(picker)"
      />
      @if (error(); as e) {
        <p class="chip risk" role="alert">{{ e }}</p>
      }
      @if (result(); as r) {
        <p role="status">
          {{ r.rows }} students on the sheet · {{ r.updated }} updated
          @if (r.skipped) { · {{ r.skipped }} rows skipped }
        </p>
        @if (r.unmatched.length) {
          <p class="chip warn">No student holds these USNs: {{ r.unmatched.join(', ') }}</p>
        }
      }
    </div>
  `,
  styles: `
    .visually-hidden-input {
      position: absolute;
      width: 1px;
      height: 1px;
      overflow: hidden;
      clip: rect(0 0 0 0);
    }
  `,
})
export class AdminRolePreferencesComponent {
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly result = signal<ImportResult | null>(null);

  async upload(input: HTMLInputElement): Promise<void> {
    const picked = input.files?.[0];
    input.value = '';
    if (!picked) return;
    this.error.set(null);
    this.result.set(null);
    const file = await readPickedFile(picked);
    if (!file) {
      this.error.set(UNREADABLE_FILE_MESSAGE);
      return;
    }
    const body = new FormData();
    body.append('file', file, file.name);
    this.busy.set(true);
    try {
      const res = await fetch(`${environment.apiBase}/admin/role-preferences/import`, {
        method: 'POST',
        credentials: 'include',
        body,
      });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        const detail = data?.detail;
        this.error.set(typeof detail === 'string' ? detail : `The upload was refused (${res.status}).`);
        return;
      }
      this.result.set((await res.json()) as ImportResult);
    } catch {
      this.error.set(UPLOAD_DROPPED_MESSAGE);
    } finally {
      this.busy.set(false);
    }
  }
}
