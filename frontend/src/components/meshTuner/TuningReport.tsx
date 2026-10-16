import { CircleCheck, CircleX, ExternalLink, FileText, ScrollText, ShieldAlert, ShieldCheck, TriangleAlert } from 'lucide-react'

import { API_PREFIX } from '../../api/client'
import { tuningFileUrl, tuningPreviewUrl } from '../../api/tunings'
import type { TuningCheckStatus, TuningMetric, TuningRun, TuningValidation } from '../../api/types'
import type { TunedRun } from '../../hooks/useViewedTuning'
import { formatDuration } from '../../lib/format'
import { formatCheckValue, formatMetricValue, metricChange, pairPreviews, type PreviewPair } from '../../lib/meshTuner'
import { Tag } from '../common/Tag'
import { TunedDownloadButton } from './TunedDownloadButton'
import styles from './TuningReport.module.css'

function CheckIcon({ status }: { status: TuningCheckStatus }) {
  if (status === 'pass') return <CircleCheck size={14} aria-hidden />
  if (status === 'warn') return <TriangleAlert size={14} aria-hidden />
  return <CircleX size={14} aria-hidden />
}

const CHECK_TEXT: Record<TuningCheckStatus, string> = { pass: 'passed', warn: 'warning', fail: 'failed' }

const PREVIEW_ROWS = [
  { variant: 'before', label: 'Before' },
  { variant: 'after', label: 'After' },
] as const

/** Before/after renders: one column per view, the two rows stacked so each view compares vertically. */
function PreviewGrid({ pairs }: { pairs: PreviewPair[] }) {
  const columns = { gridTemplateColumns: `44px repeat(${pairs.length}, minmax(0, 1fr))` }
  return (
    <section className={styles.section} aria-label="Before and after previews">
      <h4 className={styles.heading}>Previews</h4>
      <div className={styles.previewGrid} style={columns}>
        <span aria-hidden />
        {pairs.map((pair) => (
          <span key={pair.view} className={styles.previewView} aria-hidden>
            {pair.label}
          </span>
        ))}
        {PREVIEW_ROWS.map((row) => (
          <div key={row.variant} className={styles.previewRow}>
            <span className={styles.previewLabel} aria-hidden>
              {row.label}
            </span>
            {pairs.map((pair) => {
              const preview = row.variant === 'before' ? pair.before : pair.after
              return preview ? (
                <a
                  key={pair.view}
                  className={styles.thumb}
                  href={tuningPreviewUrl(preview)}
                  target="_blank"
                  rel="noopener noreferrer"
                  title={`Open the ${pair.label} view ${row.label.toLowerCase()} Auto Tune`}
                >
                  <img
                    src={tuningPreviewUrl(preview)}
                    alt={`${pair.label} view ${row.label.toLowerCase()} Auto Tune`}
                    loading="lazy"
                    decoding="async"
                  />
                </a>
              ) : (
                <span key={pair.view} className={`${styles.thumb} ${styles.thumbMissing}`}>
                  —
                </span>
              )
            })}
          </div>
        ))}
      </div>
    </section>
  )
}

function MetricsTable({ metrics }: { metrics: TuningMetric[] }) {
  return (
    <details className={styles.details} open>
      <summary className={styles.summary}>
        Mesh metrics <span className={styles.count}>{metrics.length}</span>
      </summary>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <caption className="visually-hidden">Mesh metrics before and after Auto Tune</caption>
          <thead>
            <tr>
              <th scope="col">Metric</th>
              <th scope="col">Before</th>
              <th scope="col">After</th>
              <th scope="col">Change</th>
            </tr>
          </thead>
          <tbody>
            {metrics.map((metric) => {
              const change = metricChange(metric)
              return (
                <tr key={metric.key}>
                  <th scope="row">{metric.label}</th>
                  <td>{formatMetricValue(metric, metric.before)}</td>
                  <td>{formatMetricValue(metric, metric.after)}</td>
                  <td className={styles[change.trend]}>
                    <span className={styles.delta}>{change.text}</span>
                    {change.relative && <span className={styles.relative}>{change.relative}</span>}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </details>
  )
}

function ValidationChecks({ validation }: { validation: TuningValidation }) {
  return (
    <details className={styles.details} open>
      <summary className={styles.summary}>
        Validation
        <Tag tone={validation.passed ? 'success' : 'warning'} className={styles.summaryTag}>
          {validation.passed ? 'Passed' : 'Needs review'}
        </Tag>
      </summary>
      <ul className={styles.checks}>
        {validation.checks.map((check) => (
          <li key={check.id} className={`${styles.check} ${styles[`check_${check.status}`]}`}>
            <span className={styles.checkIcon}>
              <CheckIcon status={check.status} />
            </span>
            <span className={styles.checkText}>
              <span className={styles.checkLabel}>
                {check.label}
                <span className="visually-hidden"> — {CHECK_TEXT[check.status]}</span>
              </span>
              {check.message && <span className={styles.checkMessage}>{check.message}</span>}
            </span>
            {(check.value !== null || check.threshold !== null) && (
              <span className={styles.checkValue}>
                {formatCheckValue(check.value)}
                {check.threshold !== null && <span className={styles.threshold}> / {formatCheckValue(check.threshold)}</span>}
              </span>
            )}
          </li>
        ))}
      </ul>
    </details>
  )
}

/** Result of a finished run: summary, previews, metrics table, validation checks, warnings, downloads. */
export function TuningReport({ run }: { run: TuningRun }) {
  const tuned = run.status === 'completed' && run.result ? (run as TunedRun) : null
  const pairs = run.result ? pairPreviews(run.result.previews) : []
  const logUrl = tuningFileUrl(run.result?.log_url ?? `${API_PREFIX}/tunings/${encodeURIComponent(run.id)}/log`)
  const reportUrl = run.result ? tuningFileUrl(run.result.report_url) : null
  const duration =
    run.started_at && run.finished_at ? (Date.parse(run.finished_at) - Date.parse(run.started_at)) / 1000 : null

  return (
    <div className={styles.report}>
      {run.status === 'failed' && (
        <div className={styles.errorBox} role="alert">
          <p className={styles.errorCode}>{run.error?.code ?? 'error'}</p>
          <p className={styles.errorMessage}>{run.error?.message ?? 'Auto Tune failed.'}</p>
        </div>
      )}

      {tuned && (
        <div className={styles.summaryBox}>
          {run.validation && !run.validation.passed ? (
            <ShieldAlert size={18} className={styles.summaryWarn} aria-hidden />
          ) : (
            <ShieldCheck size={18} className={styles.summaryOk} aria-hidden />
          )}
          <div className={styles.summaryText}>
            <p className={styles.summaryTitle}>
              {run.warnings.length > 0 || (run.validation && !run.validation.passed)
                ? 'Tuned model ready — review the notes below'
                : 'Tuned model ready'}
            </p>
            <p className={styles.summaryMeta}>
              {[
                run.blender ? `Blender ${run.blender.version}` : null,
                duration !== null ? formatDuration(duration) : null,
                `${run.options.preset} preset`,
              ]
                .filter(Boolean)
                .join(' · ')}
            </p>
          </div>
        </div>
      )}

      {pairs.length > 0 && <PreviewGrid pairs={pairs} />}
      {run.metrics.length > 0 && <MetricsTable metrics={run.metrics} />}
      {run.validation && run.validation.checks.length > 0 && <ValidationChecks validation={run.validation} />}

      {run.warnings.length > 0 && (
        <section className={styles.section} aria-label="Warnings">
          <h4 className={styles.heading}>Warnings</h4>
          <ul className={styles.warnings}>
            {run.warnings.map((warning) => (
              <li key={warning}>
                <TriangleAlert size={13} aria-hidden />
                <span>{warning}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {tuned && <TunedDownloadButton run={tuned} />}

      <div className={styles.links}>
        {reportUrl && (
          <a href={reportUrl} target="_blank" rel="noopener noreferrer">
            <FileText size={13} aria-hidden /> Report JSON <ExternalLink size={11} aria-hidden />
          </a>
        )}
        {run.status !== 'cancelled' && (
          <a href={logUrl} target="_blank" rel="noopener noreferrer">
            <ScrollText size={13} aria-hidden /> Blender log <ExternalLink size={11} aria-hidden />
          </a>
        )}
      </div>
    </div>
  )
}
