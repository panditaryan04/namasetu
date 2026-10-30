import { Link } from "react-router-dom";

export default function NotFound() {
  return (
    <div className="py-16 text-center">
      <h1 className="text-2xl font-bold">Page not found</h1>
      <p className="mt-2 text-slate-600 dark:text-slate-400">This page doesn&apos;t exist.</p>
      <Link to="/" className="mt-4 inline-block font-medium text-teal-700 underline dark:text-teal-400">
        Back to search
      </Link>
    </div>
  );
}
