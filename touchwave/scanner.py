"""Clock-independent bookmark scanning state machine."""
class Scanner:
    def __init__(self):
        self.active = False
        self.items = []
        self.index = -1
        self.dwell = 1.0
        self.hold = 2.0
        self.deadline = 0
        self.settle_until = 0

    def start(self, items, now, dwell=1.0, hold=2.0):
        if not items:
            raise ValueError("No bookmarks in this scan group")
        self.items, self.dwell, self.hold = items, max(0.3, dwell), max(0, hold)
        self.active, self.index, self.deadline = True, -1, now

    def stop(self):
        self.active = False

    def tick(self, now, level, threshold):
        if not self.active:
            return None
        if now >= self.settle_until and level > threshold and self.index >= 0:
            self.deadline = max(self.deadline, now + self.hold)
        if now < self.deadline:
            return None
        self.index = (self.index + 1) % len(self.items)
        self.deadline = now + self.dwell
        self.settle_until = now + 0.25
        return self.items[self.index]
