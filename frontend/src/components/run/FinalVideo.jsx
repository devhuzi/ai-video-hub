import { useState } from "react";
import { toast } from "sonner";
import { Copy, Download, ExternalLink } from "lucide-react";
import { Button, buttonClasses } from "../ui/button";
import { isShareableUrl, mediaSrc } from "../../lib/api";
import { ASPECT_CLASS } from "./media";
import { cn } from "../../lib/utils";

export default function FinalVideo({ url, name, aspectRatio }) {
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);
  const src = mediaSrc(url);
  // Server-hosted files carry the session token in their src, so they're only
  // offered as a download — never as a link to copy or share.
  const shareable = isShareableUrl(url);
  const portrait = aspectRatio === "9:16";

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Couldn't copy the link. Your browser blocked clipboard access.");
    }
  };

  const fileName = `${(name || "video").replace(/[^\w.-]+/g, "_")}.mp4`;

  return (
    <section aria-labelledby="final-video-heading" className="rounded border border-line bg-surface">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-2.5">
        <h2 id="final-video-heading" className="text-sm font-medium text-fg">
          Final video
        </h2>
        <div className="flex gap-2">
          {shareable ? (
            <>
              <Button size="sm" onClick={copy}>
                <Copy className="h-3.5 w-3.5" aria-hidden="true" />
                {copied ? "Copied" : "Copy link"}
              </Button>
              <a href={url} target="_blank" rel="noopener noreferrer" className={buttonClasses({ size: "sm" })}>
                <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                Open
              </a>
            </>
          ) : (
            <a href={src} download={fileName} className={buttonClasses({ size: "sm" })}>
              <Download className="h-3.5 w-3.5" aria-hidden="true" />
              Download
            </a>
          )}
        </div>
      </div>
      <div className="flex justify-center bg-canvas p-2">
        {failed ? (
          <div
            className={cn(
              "flex items-center justify-center text-sm text-fg-muted",
              ASPECT_CLASS[aspectRatio] || "aspect-video",
              portrait ? "h-[50vh]" : "w-full",
            )}
          >
            Couldn&apos;t load the video.
          </div>
        ) : (
          <video
            key={src}
            src={src}
            controls
            preload="metadata"
            onError={() => setFailed(true)}
            className={cn("rounded-sm bg-canvas", portrait ? "max-h-[70vh]" : "w-full")}
          />
        )}
      </div>
    </section>
  );
}
