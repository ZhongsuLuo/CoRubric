<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api/client'
import { useI18n } from '../i18n'
import type { CaseImportJob, ImportStatus } from '../types/imports'

const { t } = useI18n()
const props = defineProps<{ disabled?: boolean }>()
const emit = defineEmits<{ 'use-case': [caseId: string] }>()
const open = ref(false)
const files = ref<File[]>([])
const folderInput = ref<HTMLInputElement | null>(null)
const name = ref('')
const skipped = ref(0)
const engine = ref<'hybrid' | 'legacy'>('hybrid')
const uploading = ref(false)
const retrying = ref('')
const error = ref('')
const jobs = ref<CaseImportJob[]>([])
let timer: ReturnType<typeof setTimeout> | undefined
let disposed = false
const activeCount = computed(
  () => jobs.value.filter((j) => ['queued', 'running'].includes(j.status)).length,
)
const terminal = (job: CaseImportJob) => !['queued', 'running'].includes(job.status)
const completed = (job: CaseImportJob) => job.files.filter((f) => f.status === 'completed').length
const failed = (job: CaseImportJob) =>
  job.files.filter((f) => ['failed', 'interrupted'].includes(f.status)).length
const percent = (job: CaseImportJob) =>
  Math.floor((100 * (completed(job) + failed(job))) / Math.max(1, job.files.length))
const statusLabel: Record<ImportStatus, string> = {
  queued: 'Queued',
  pending: 'Queued',
  running: 'Parsing',
  completed: 'Parsed',
  partial: 'Partially completed',
  failed: 'Parsing failed',
  interrupted: 'Interrupted',
}
const message = (err: unknown) => {
  const text = err instanceof Error ? err.message : t('Operation failed. Please try again.')
  try {
    return JSON.parse(text).detail || text
  } catch {
    return text
  }
}

function chooseFolder(event: Event) {
  const selected = Array.from((event.target as HTMLInputElement).files || [])
  files.value = selected.filter((file) => /\.pdf$/i.test(file.name))
  skipped.value = selected.length - files.value.length
  name.value = selected[0]?.webkitRelativePath.split('/')[0] || ''
  error.value = files.value.length ? '' : t('No PDF files in the selected folder')
}

async function refresh() {
  try {
    const result = await api.listCaseImports()
    if (!disposed) jobs.value = result.data?.items || []
  } catch (err) {
    if (!disposed && open.value)
      error.value = t('Could not update progress: {message}', { message: message(err) })
  } finally {
    if (!disposed) timer = setTimeout(refresh, 2500)
  }
}

async function upload() {
  if (!name.value.trim() || !files.value.length || uploading.value) return
  uploading.value = true
  error.value = ''
  try {
    const result = await api.uploadCaseFolder(name.value.trim(), files.value, engine.value)
    if (result.data)
      jobs.value = [result.data, ...jobs.value.filter((job) => job.id !== result.data?.id)]
    files.value = []
    if (folderInput.value) folderInput.value.value = ''
    name.value = ''
    skipped.value = 0
  } catch (err) {
    error.value = message(err)
  } finally {
    uploading.value = false
  }
}

async function retry(job: CaseImportJob) {
  retrying.value = job.id
  error.value = ''
  try {
    const response = await api.retryCaseImport(job.id)
    if (response.data) jobs.value = jobs.value.map((j) => (j.id === job.id ? response.data! : j))
  } catch (err) {
    error.value = message(err)
  } finally {
    retrying.value = ''
  }
}

function useCase(job: CaseImportJob) {
  open.value = false
  emit('use-case', job.case_id)
}
onMounted(refresh)
onUnmounted(() => {
  disposed = true
  clearTimeout(timer)
})
</script>

<template>
  <button
    type="button"
    data-testid="folder-import-open"
    class="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold hover:bg-slate-50"
    @click="open = true"
  >
    {{ t('Upload folder')
    }}<span v-if="activeCount"> · {{ t('{count} parsing', { count: activeCount }) }}</span>
  </button>
  <Teleport to="body">
    <div
      v-if="open"
      class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-6"
      @click.self="open = false"
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="folder-import-title"
        class="flex max-h-[90vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl bg-white shadow-xl"
      >
        <header class="flex items-start justify-between border-b px-6 py-4">
          <div>
            <h2 id="folder-import-title" class="text-lg font-semibold">
              {{ t('Import a PDF folder') }}
            </h2>
            <p class="mt-1 text-sm text-slate-500">
              {{
                t('Parse uploaded PDFs, then select the case to grade reports and build a rubric.')
              }}
            </p>
          </div>
          <button
            type="button"
            :aria-label="t('Close')"
            class="rounded px-2 text-xl text-slate-500"
            @click="open = false"
          >
            ×
          </button>
        </header>
        <div class="space-y-6 overflow-y-auto p-6">
          <form class="space-y-4 rounded-xl border bg-slate-50 p-4" @submit.prevent="upload">
            <label class="block text-sm font-medium"
              >{{ t('Choose folder (including subfolders)') }}
              <input
                ref="folderInput"
                data-testid="folder-input"
                type="file"
                webkitdirectory
                multiple
                :disabled="uploading"
                class="mt-2 block w-full text-sm"
                @change="chooseFolder"
              />
            </label>
            <p v-if="files.length || skipped" class="text-xs text-slate-500">
              {{
                t('{count} PDFs selected; {skipped} other files skipped.', {
                  count: files.length,
                  skipped,
                })
              }}
              {{ t('Up to 200 PDFs, 100 MB per file, 500 MB total.') }}
            </p>
            <div class="grid gap-4 sm:grid-cols-2">
              <label class="text-sm font-medium"
                >{{ t('Case name') }}
                <input
                  v-model="name"
                  data-testid="import-name"
                  required
                  :disabled="uploading"
                  :placeholder="t('Defaults to the folder name')"
                  class="mt-1 block w-full rounded-lg border bg-white px-3 py-2"
                />
              </label>
              <label class="text-sm font-medium"
                >{{ t('Parser') }}
                <select
                  v-model="engine"
                  :disabled="uploading"
                  class="mt-1 block w-full rounded-lg border bg-white px-3 py-2"
                >
                  <option value="hybrid">
                    {{ t('OCR + regional image enhancement (recommended)') }}
                  </option>
                  <option value="legacy">{{ t('Legacy parser') }}</option>
                </select>
              </label>
            </div>
            <button
              data-testid="import-submit"
              type="submit"
              :disabled="uploading || !files.length || !name.trim()"
              class="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40"
            >
              {{ uploading ? t('Uploading, please keep this page open…') : t('Upload and parse') }}
            </button>
            <p class="text-xs text-slate-500">
              {{
                t(
                  'After upload, you may close this dialog. Parsing continues in the background; grading starts separately.',
                )
              }}
            </p>
          </form>
          <p v-if="error" role="alert" class="rounded-lg bg-red-50 p-3 text-sm text-red-700">
            {{ error }}
          </p>
          <div class="space-y-4">
            <h3 class="font-semibold">{{ t('Parsing history') }}</h3>
            <p v-if="!jobs.length" class="text-sm text-slate-500">{{ t('No parsing jobs yet') }}</p>
            <article
              v-for="job in jobs"
              :key="job.id"
              class="space-y-3 rounded-xl border p-4"
              :data-testid="`import-job-${job.id}`"
            >
              <div class="flex items-center justify-between gap-3">
                <div>
                  <h4 class="font-semibold">{{ job.case_id }}</h4>
                  <p class="text-xs text-slate-500">
                    {{ new Date(job.created_at).toLocaleString() }} ·
                    {{ t(statusLabel[job.status]) }}
                  </p>
                </div>
                <button
                  v-if="completed(job) > 0 && terminal(job)"
                  type="button"
                  :disabled="props.disabled"
                  class="rounded-lg bg-emerald-50 px-3 py-2 text-sm font-semibold text-emerald-800 disabled:opacity-40"
                  data-testid="import-use-case"
                  @click="useCase(job)"
                >
                  {{ t('Use this case') }}
                </button>
              </div>
              <progress
                :value="percent(job)"
                max="100"
                class="h-2 w-full"
                :aria-label="t('Parsing progress for {name}', { name: job.case_id })"
              />
              <p class="text-xs text-slate-600" aria-live="polite">
                {{
                  t('Completed {count}/{total} files', {
                    count: completed(job),
                    total: job.files.length,
                  })
                }}<span v-if="failed(job)">
                  · {{ t('{count} failed or interrupted', { count: failed(job) }) }}</span
                >
              </p>
              <ul class="max-h-52 space-y-2 overflow-y-auto text-sm">
                <li v-for="file in job.files" :key="file.id" class="rounded bg-slate-50 px-3 py-2">
                  <div class="flex justify-between gap-4">
                    <span class="break-all">{{ file.path }}</span
                    ><span class="shrink-0 text-xs text-slate-500"
                      >{{ file.stage }} · {{ t('{count} pages', { count: file.pages }) }}</span
                    >
                  </div>
                  <p v-if="file.error" class="mt-1 text-xs text-red-700">{{ file.error }}</p>
                </li>
              </ul>
              <div class="flex items-center justify-between gap-4">
                <details class="text-xs text-slate-500">
                  <summary class="cursor-pointer">{{ t('View progress log') }}</summary>
                  <ol class="mt-2 max-h-40 space-y-1 overflow-y-auto">
                    <li v-for="(event, i) in job.events" :key="i">
                      {{ new Date(event.time).toLocaleTimeString() }} · {{ event.message }}
                    </li>
                  </ol>
                </details>
                <button
                  v-if="terminal(job) && job.status !== 'completed'"
                  type="button"
                  :disabled="Boolean(retrying)"
                  class="rounded border px-3 py-1.5 text-xs disabled:opacity-40"
                  @click="retry(job)"
                >
                  {{ retrying === job.id ? t('Submitting…') : t('Retry unfinished files') }}
                </button>
              </div>
            </article>
          </div>
        </div>
      </section>
    </div>
  </Teleport>
</template>
