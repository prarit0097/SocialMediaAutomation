def pixel_events(request):
    if not hasattr(request, "session"):
        return {"pixel_events": []}

    events = list(request.session.pop("pixel_events", []) or [])
    if events:
        request.session.modified = True
    return {"pixel_events": events}
