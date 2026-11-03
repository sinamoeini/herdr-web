export class SplitRatioWriter {
  constructor(persist, refresh, changed, failed) {
    this.persist = persist;
    this.refresh = refresh;
    this.changed = changed;
    this.failed = failed;
    this.pending = null;
    this.revision = 0;
    this.queue = Promise.resolve();
  }

  save(split, value) {
    const ratio = Math.max(.1, Math.min(.9, value));
    const revision = ++this.revision;
    const params = {tab_id: split.tab, path: [...split.path], ratio};
    this.pending = ratio;
    this.changed();
    this.queue = this.queue.then(async () => {
      try {
        await this.persist(params);
        await this.refresh();
      } catch (error) {
        this.failed(error);
      } finally {
        if (revision === this.revision) {
          this.pending = null;
          this.changed();
        }
      }
    });
    return this.queue;
  }
}
