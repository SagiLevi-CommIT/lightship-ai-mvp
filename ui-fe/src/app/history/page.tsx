"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  listJobs,
  getPipelineResult,
  type Job,
  type PipelineResultJson,
} from "@/lib/api-client";
import { ResultsView } from "@/components/evaluation/results-view";
import {
  Clock,
  CheckCircle2,
  XCircle,
  Loader2,
  RefreshCw,
} from "lucide-react";
import { cn } from "@/lib/utils";

export default function HistoryPage() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const [result, setResult] = useState<PipelineResultJson | null>(null);
  const [resultLoading, setResultLoading] = useState(false);

  const fetchJobs = async () => {
    setLoading(true);
    try {
      const data = await listJobs();
      setJobs(data);
    } catch (err) {
      console.error("Failed to load jobs:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchJobs();
  }, []);

  const viewResult = async (job: Job) => {
    setSelectedJob(job);
    setResult(null);
    if (job.status === "COMPLETED") {
      setResultLoading(true);
      try {
        const data = await getPipelineResult(job.job_id);
        setResult(data);
      } catch (err) {
        console.error("Failed to load result:", err);
      } finally {
        setResultLoading(false);
      }
    }
  };

  const statusIcon = (status: string) => {
    switch (status) {
      case "COMPLETED":
        return <CheckCircle2 className="h-4 w-4 text-green-500" />;
      case "FAILED":
        return <XCircle className="h-4 w-4 text-red-500" />;
      case "PROCESSING":
        return <Loader2 className="h-4 w-4 text-brand-500 animate-spin" />;
      default:
        return <Clock className="h-4 w-4 text-gray-400" />;
    }
  };

  return (
    <div className="mx-auto max-w-7xl px-6 py-8 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-900">Run History</h1>
        <button
          onClick={fetchJobs}
          className="flex items-center gap-1.5 text-sm text-brand-600 hover:underline"
        >
          <RefreshCw className="h-3.5 w-3.5" />
          Refresh
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Job list */}
        <div className="lg:col-span-1">
          <div className="rounded-lg border bg-white overflow-hidden">
            <div className="px-4 py-3 border-b bg-gray-50 text-sm font-medium text-gray-700">
              Jobs ({jobs.length})
            </div>
            {loading ? (
              <div className="p-8 text-center text-gray-400">
                <Loader2 className="h-5 w-5 mx-auto animate-spin" />
              </div>
            ) : jobs.length === 0 ? (
              <div className="p-8 text-center text-gray-400 text-sm">
                No jobs found
              </div>
            ) : (
              <ul className="divide-y max-h-[70vh] overflow-y-auto">
                {jobs.map((job) => (
                  <li key={job.job_id}>
                    <button
                      onClick={() => viewResult(job)}
                      className={cn(
                        "w-full text-left px-4 py-3 hover:bg-gray-50 transition-colors",
                        selectedJob?.job_id === job.job_id && "bg-brand-50"
                      )}
                    >
                      <div className="flex items-center gap-2">
                        {statusIcon(job.status)}
                        <span className="text-sm font-medium text-gray-800 truncate flex-1">
                          {job.filename || job.job_id.slice(0, 8)}
                        </span>
                      </div>
                      <div className="mt-1 flex items-center gap-2 text-xs text-gray-400">
                        <span>{job.status}</span>
                        {job.created_at && (
                          <>
                            <span>·</span>
                            <span>
                              {new Date(job.created_at).toLocaleDateString()}{" "}
                              {new Date(job.created_at).toLocaleTimeString()}
                            </span>
                          </>
                        )}
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        {/* Detail / Results */}
        <div className="lg:col-span-2">
          {!selectedJob ? (
            <div className="rounded-lg border bg-white p-12 text-center text-gray-400 text-sm">
              Select a job from the list to view details
            </div>
          ) : resultLoading ? (
            <div className="rounded-lg border bg-white p-12 text-center text-gray-400">
              <Loader2 className="h-6 w-6 mx-auto animate-spin mb-2" />
              Loading results…
            </div>
          ) : result ? (
            <ResultsView
              result={result}
              jobId={selectedJob.job_id}
              filename={selectedJob.filename || ""}
            />
          ) : (
            <div className="rounded-lg border bg-white p-8 space-y-3">
              <h3 className="text-sm font-medium text-gray-700">
                Job: {selectedJob.job_id}
              </h3>
              <p className="text-sm text-gray-500">
                Status: {selectedJob.status}
              </p>
              {selectedJob.error_message && (
                <p className="text-sm text-red-600">
                  Error: {selectedJob.error_message}
                </p>
              )}
              {selectedJob.status !== "COMPLETED" && (
                <p className="text-sm text-gray-400">
                  Results are available once processing completes.
                </p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
