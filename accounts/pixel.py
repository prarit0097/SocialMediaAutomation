def queue_pixel_event(request, event_name, payload=None, custom=False):
    events = list(request.session.get("pixel_events") or [])
    events.append(
        {
            "name": str(event_name or "").strip(),
            "payload": payload or {},
            "custom": bool(custom),
        }
    )
    request.session["pixel_events"] = [event for event in events if event.get("name")]
    request.session.modified = True
