import { Link } from "react-router-dom";
import { buttonClasses } from "../components/ui/button";

export default function NotFound() {
  return (
    <div className="px-4 py-16 text-center md:px-6">
      <p className="text-base font-medium text-fg">Page not found</p>
      <p className="mt-1 text-sm text-fg-secondary">There&apos;s nothing at this address.</p>
      <Link to="/" className={buttonClasses({ className: "mt-4" })}>
        Back to queue
      </Link>
    </div>
  );
}
