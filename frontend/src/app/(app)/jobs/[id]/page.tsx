"use client";
import Link from "next/link";
import { use } from "react";
import { JobView } from "@/components/job/JobView";
import { applyReviews, reviewHunk } from "@/lib/api/jobs";
import { useJob } from "@/lib/useJob";

export default function JobPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { job, error, refresh } = useJob(id);

  if (!job) {
    if (error?.status === 401)
      return (
        <div className="wrap pb-16 pt-10">
          <h1 className="t-h1">Your session has ended</h1>
          <Link href={`/signin?next=/jobs/${id}`} className="btn btn-accent mt-6">Sign in with GitHub</Link>
        </div>
      );
    if (error?.status === 404)
      return (
        <div className="wrap pb-16 pt-10">
          <h1 className="t-h1">No such sync in your workspace</h1>
          <Link href="/repos" className="link mt-4 inline-block">See your repositories</Link>
        </div>
      );
    return (
      <div className="wrap pb-16 pt-10" aria-busy={!error}>
        {error ? <p role="alert" className="text-danger">{error.message}</p> : <p className="text-muted">Loading the sync…</p>}
      </div>
    );
  }

  return (
    <>
      {error && <p role="alert" className="wrap pt-4 t-label text-danger">Lost contact with Forkward: {error.message} Retrying.</p>}
      <JobView
        job={job}
        onReview={async (h, decision, resolution) => {
          await reviewHunk(job.id, h.id, decision, resolution);
          await refresh();
        }}
        onApply={async () => {
          await applyReviews(job.id);
          await refresh();
        }}
      />
    </>
  );
}
