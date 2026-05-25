"""Tkinter GUI 界面模块"""

import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

from extractor import process_pdf
from processor import group_and_sort, match_forwarder_to_amazon, process_and_output


class RemarksDialog:
    """中文备注编辑弹窗（含目的地仓库输入）"""

    def __init__(self, parent, sku_counts, existing_remarks=None, existing_dest=None, has_unmatched_fwd=False):
        self.result = None
        self.dest_result = None
        self.remarks = dict(existing_remarks) if existing_remarks else {}
        self.entries = {}
        self.sku_counts = sku_counts  # {sku: page_count}
        self.has_unmatched_fwd = has_unmatched_fwd

        self.dialog = tk.Toplevel(parent)
        self.dialog.title("编辑中文备注")
        self.dialog.geometry("520x520")
        self.dialog.transient(parent)
        self.dialog.grab_set()

        self._build_ui(list(sku_counts.keys()), existing_dest)
        self.dialog.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - 520) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - 520) // 2
        self.dialog.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _build_ui(self, skus, existing_dest):
        header = ttk.Frame(self.dialog, padding=10)
        header.pack(fill=tk.X)
        ttk.Label(header, text="为每个 SKU 输入中文备注（留空则使用原 SKU）：",
                  font=("", 10)).pack(anchor=tk.W)

        container = ttk.Frame(self.dialog)
        container.pack(fill=tk.BOTH, expand=True, padx=10)

        canvas = tk.Canvas(container)
        scrollbar = ttk.Scrollbar(container, orient=tk.VERTICAL, command=canvas.yview)
        scrollable = ttk.Frame(canvas)

        scrollable.bind("<Configure>",
                        lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable, anchor=tk.NW)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind("<MouseWheel>", _on_mousewheel)

        self.warn_labels = {}

        for i, sku in enumerate(sorted(skus)):
            row = ttk.Frame(scrollable)
            row.pack(fill=tk.X, pady=3)

            count = self.sku_counts.get(sku, 1)
            label_text = f"{sku} ({count}页)"
            ttk.Label(row, text=label_text, width=26, anchor=tk.W).pack(side=tk.LEFT, padx=(0, 8))
            entry = ttk.Entry(row, width=28)
            entry.pack(side=tk.LEFT)
            if sku in self.remarks:
                entry.insert(0, self.remarks[sku])
            self.entries[sku] = entry

            warn = tk.Label(row, text="", fg="red", font=("", 8))
            warn.pack(side=tk.LEFT, padx=(5, 0))
            self.warn_labels[sku] = warn

            entry.bind("<KeyRelease>", lambda e, s=sku: self._check_length(s))

        # 目的地仓库输入（仅当有未匹配的货代标签时显示）
        self.dest_entry = None
        if self.has_unmatched_fwd:
            dest_frame = ttk.LabelFrame(self.dialog, text="货代标签目的地仓库（未匹配时填写）", padding=10)
            dest_frame.pack(fill=tk.X, padx=10, pady=(5, 0))

            hint = ttk.Label(dest_frame,
                text="如果货代标签未匹配到 FBA 标签，请在此输入仓库代码（如 TEB9、LAX9）：",
                font=("", 9), foreground="#666666")
            hint.pack(anchor=tk.W, pady=(0, 5))

            dest_row = ttk.Frame(dest_frame)
            dest_row.pack(fill=tk.X)
            ttk.Label(dest_row, text="目的地仓库：", font=("", 10)).pack(side=tk.LEFT)
            self.dest_entry = ttk.Entry(dest_row, width=20)
            self.dest_entry.pack(side=tk.LEFT, padx=(5, 0))
            if existing_dest:
                self.dest_entry.insert(0, existing_dest)

        btn_frame = ttk.Frame(self.dialog, padding=10)
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="确定", command=self._on_ok, width=10).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="取消", command=self.dialog.destroy, width=10).pack(side=tk.RIGHT)

    def _on_ok(self):
        # 检查是否有超长输入
        for sku, entry in self.entries.items():
            val = entry.get().strip()
            if len(val) > 10:
                self.warn_labels[sku].config(text="只能输入10个文字")
                return
        self.result = {}
        for sku, entry in self.entries.items():
            val = entry.get().strip()
            if val:
                self.result[sku] = val
        if self.dest_entry:
            self.dest_result = self.dest_entry.get().strip().upper() or None

    def _check_length(self, sku):
        entry = self.entries[sku]
        warn = self.warn_labels[sku]
        val = entry.get()
        if len(val) > 10:
            warn.config(text="只能输入10个文字")
        else:
            warn.config(text="")
        self.dialog.destroy()


class LogWindow:
    """日志输出窗口"""

    def __init__(self, parent):
        self.window = tk.Toplevel(parent)
        self.window.title("处理日志")
        self.window.geometry("600x300")
        self.window.withdraw()

        self.text = scrolledtext.ScrolledText(self.window, wrap=tk.WORD, state=tk.DISABLED)
        self.text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def show(self):
        self.window.deiconify()
        self.window.lift()

    def log(self, message):
        self.text.configure(state=tk.NORMAL)
        self.text.insert(tk.END, message + "\n")
        self.text.see(tk.END)
        self.text.configure(state=tk.DISABLED)

    def clear(self):
        self.text.configure(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)
        self.text.configure(state=tk.DISABLED)


class App:
    """主应用窗口"""

    def __init__(self, root):
        self.root = root
        self.root.title("亚马逊外箱标签智能分组系统")
        self.root.geometry("920x620")
        self.root.minsize(800, 500)

        self.pdf_files = []
        self.extracted_data = []
        self.exceptions = []
        self.remarks = {}
        self.output_dir = ""

        self._build_ui()

    def _build_ui(self):
        # 顶部按钮栏
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill=tk.X)

        ttk.Button(top, text="导入 PDF", command=self._import_pdfs).pack(side=tk.LEFT, padx=3)
        ttk.Button(top, text="编辑中文备注", command=self._edit_remarks).pack(side=tk.LEFT, padx=3)
        ttk.Button(top, text="处理并输出", command=self._process_output).pack(side=tk.LEFT, padx=3)
        ttk.Button(top, text="查看日志", command=self._show_log).pack(side=tk.LEFT, padx=3)
        ttk.Button(top, text="清空重置", command=self._reset).pack(side=tk.RIGHT, padx=3)

        # 中间区域 - 左右分栏
        middle = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        middle.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # 左侧：文件列表
        left = ttk.LabelFrame(middle, text="已导入的 PDF 文件", padding=5)
        middle.add(left, weight=1)

        self.file_listbox = tk.Listbox(left, font=("", 9))
        file_scroll = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.file_listbox.yview)
        self.file_listbox.configure(yscrollcommand=file_scroll.set)
        self.file_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        file_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # 右侧：提取结果表格
        right = ttk.LabelFrame(middle, text="提取结果", padding=5)
        middle.add(right, weight=3)

        columns = ("type", "sku", "remark", "destination", "box", "status")
        self.tree = ttk.Treeview(right, columns=columns, show="headings", height=15)

        self.tree.heading("type", text="类型")
        self.tree.heading("sku", text="SKU / FNSKU")
        self.tree.heading("remark", text="中文备注")
        self.tree.heading("destination", text="目的地仓库")
        self.tree.heading("box", text="箱号/序号")
        self.tree.heading("status", text="状态")

        self.tree.column("type", width=50, minwidth=40)
        self.tree.column("sku", width=140, minwidth=90)
        self.tree.column("remark", width=100, minwidth=70)
        self.tree.column("destination", width=90, minwidth=55)
        self.tree.column("box", width=80, minwidth=50)
        self.tree.column("status", width=80, minwidth=50)

        tree_scroll = ttk.Scrollbar(right, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # 占位提示文字（无数据时显示）
        placeholder_text = (
            "1. 填写中文备注\n"
            "请为每个SKU录入对应的中文备注。\n该备注将直接显示在标签上，作为给工厂的识别信息。\n\n"
            "2. 自动分组与排序\n"
            "系统将根据SKU进行分类、分组并生成顺序，\n确保与货袋标签精准对应。\n\n"
            "注意事项：\n"
            "请务必遵守「一箱一SKU」原则，\n每个箱子只能装有一个SKU。\n只支持10*10 PDF（一页一码）。\n请勿修改货件名称。"
        )
        self.placeholder = tk.Label(
            right, text=placeholder_text,
            font=("", 10), fg="#AAAAAA", bg="#FFFFFF",
            justify=tk.LEFT, anchor=tk.CENTER
        )
        self.placeholder.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        # 底部状态栏
        bottom = ttk.Frame(self.root, padding=(10, 5))
        bottom.pack(fill=tk.X)

        self.progress = ttk.Progressbar(bottom, mode="determinate")
        self.progress.pack(fill=tk.X, pady=(0, 5))

        self.status_var = tk.StringVar(value="就绪 — 请导入亚马逊外箱标签 PDF 文件")
        ttk.Label(bottom, textvariable=self.status_var, font=("", 9)).pack(anchor=tk.W)

        # 日志窗口
        self.log_window = LogWindow(self.root)

    def _set_status(self, text):
        self.status_var.set(text)
        self.root.update_idletasks()

    def _import_pdfs(self):
        files = filedialog.askopenfilenames(
            title="选择亚马逊外箱标签 PDF（可多次导入）",
            filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")]
        )
        if not files:
            return

        # 增量导入，跳过已存在的文件
        existing = set(self.pdf_files)
        new_files = [f for f in files if f not in existing]
        if not new_files:
            messagebox.showinfo("提示", "所选文件已全部导入。")
            return

        self.pdf_files.extend(new_files)
        for f in new_files:
            self.file_listbox.insert(tk.END, os.path.basename(f))

        self._set_status(f"已导入 {len(self.pdf_files)} 个 PDF（新增 {len(new_files)} 个），正在提取数据...")
        self.log_window.log(f"新增 {len(new_files)} 个 PDF，共 {len(self.pdf_files)} 个")

        threading.Thread(target=self._extract_all, daemon=True).start()

    def _extract_all(self):
        """后台线程提取所有 PDF 数据"""
        self.extracted_data = []
        self.exceptions = []

        import fitz
        total_files = len(self.pdf_files)
        total_pages = 0
        for fpath in self.pdf_files:
            try:
                doc = fitz.open(fpath)
                total_pages += len(doc)
                doc.close()
            except:
                pass

        self.root.after(0, lambda: setattr(self.progress, 'maximum', max(total_pages, 1)))
        self.root.after(0, lambda: setattr(self.progress, 'value', 0))

        done_pages = 0
        for i, fpath in enumerate(self.pdf_files):
            fname = os.path.basename(fpath)
            self.status_var.set(f"正在扫描 ({i + 1}/{total_files})：{fname}")
            self.log_window.log(f"处理：{fname}")

            try:
                def _page_done(cur, tot, _fname=fname, _idx=i+1):
                    nonlocal done_pages
                    done_pages += 1
                    self.progress['value'] = done_pages
                    self.status_var.set(f"正在扫描 ({_idx}/{total_files})：{_fname} — 第 {cur}/{tot} 页")

                results, excs = process_pdf(fpath, page_callback=_page_done)
                self.extracted_data.extend(results)
                self.exceptions.extend(excs)

                ok = len(results)
                fail = len(excs)
                self.log_window.log(f"  成功提取 {ok} 页，异常 {fail} 页")
            except Exception as e:
                self.log_window.log(f"  错误：{e}")

        self.root.after(0, self._on_extract_done)

    def _on_extract_done(self):
        # 提取完成后立即匹配货代标签
        has_forwarder = any(p.get('label_type') == 'forwarder' for p in self.extracted_data)
        if has_forwarder:
            match_forwarder_to_amazon(self.extracted_data, self.remarks)

        self._refresh_tree()

        fba_pages = [p for p in self.extracted_data if p.get('label_type', 'amazon') != 'forwarder']
        fwd_pages = [p for p in self.extracted_data if p.get('label_type') == 'forwarder']
        skus = set(p['sku'] for p in fba_pages if p.get('sku'))
        n_pages = len(self.extracted_data) + len(self.exceptions)

        status = f"提取完成：共 {n_pages} 页，FBA {len(fba_pages)} 页（{len(skus)} 个 SKU）"
        if fwd_pages:
            matched = len([p for p in fwd_pages if p.get('matched_fba')])
            status += f"，货代 {len(fwd_pages)} 页（{matched} 个已匹配）"
        if self.exceptions:
            status += f"，异常 {len(self.exceptions)} 页"
        self._set_status(status)
        self.log_window.log(status)
        self.progress['value'] = 100

        if self.exceptions:
            messagebox.showwarning(
                "提取完成",
                f"共 {n_pages} 页：{len(self.extracted_data)} 页成功，"
                f"{len(self.exceptions)} 页无法识别。\n"
                f"无法识别的页面将归入「异常_需人工核对.pdf」。"
            )

    def _refresh_tree(self):
        """刷新结果表格 - 按类型和 SKU 去重合并展示"""
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.placeholder.place_forget()

        # FBA 标签汇总
        fba_info = {}
        fwd_count = 0
        for p in self.extracted_data:
            if p.get('label_type') == 'forwarder':
                fwd_count += 1
                continue
            raw_sku = (p.get('sku') or '').strip()
            if not raw_sku:
                continue
            key = raw_sku.upper()
            if key not in fba_info:
                fba_info[key] = {
                    'display': raw_sku,
                    'count': 0,
                    'destinations': set(),
                }
            fba_info[key]['count'] += 1
            if p.get('destination'):
                fba_info[key]['destinations'].add(p['destination'])

        for key in sorted(fba_info.keys()):
            info = fba_info[key]
            sku = info['display']
            remark = self.remarks.get(sku, "")
            dests = ", ".join(sorted(info['destinations'])) if info['destinations'] else "-"
            box_str = f"共{info['count']}箱"
            self.tree.insert("", tk.END, values=(
                "FBA", sku, remark, dests, box_str, "正常"
            ))

        # 货代标签（显示匹配到的 FBA 信息）
        for p in self.extracted_data:
            if p.get('label_type') != 'forwarder':
                continue
            fba = p.get('matched_fba')
            # 显示对应的 FBA SKU，而不是 OCR 读到的乱码
            if fba:
                sku = fba.get('sku') or "-"
            else:
                sku = p.get('sku') or "-"
            dest = p.get('destination') or "-"
            seq = p.get('seq_num')
            seq_total = p.get('seq_total')
            box_str = f"序号 {seq}/{seq_total}" if seq else "-"
            remark = p.get('remark', '')
            status = "已匹配" if fba else "待匹配"
            self.tree.insert("", tk.END, values=(
                "货代", sku, remark, dest, box_str, status
            ))

        # 异常页面
        for p in self.exceptions:
            sku = p.get('sku') or "无法识别"
            err = p.get('error', '字段提取失败')
            label_type = "货代" if p.get('label_type') == 'forwarder' else "FBA"
            self.tree.insert("", tk.END, values=(
                label_type, sku, "", p.get('destination', '-'),
                "-", f"异常: {err[:20]}"
            ))

    def _edit_remarks(self):
        if not self.extracted_data:
            messagebox.showinfo("提示", "请先导入并提取 PDF 数据。")
            return

        # 统计每个 SKU 的页数
        sku_counts = {}
        for p in self.extracted_data:
            sku = p.get('sku')
            if sku:
                sku_counts[sku] = sku_counts.get(sku, 0) + 1

        if not sku_counts:
            messagebox.showinfo("提示", "未识别到任何 SKU，请检查 PDF 文件。")
            return

        # 检查是否有未匹配的货代标签
        has_forwarder = any(p.get('label_type') == 'forwarder' for p in self.extracted_data)
        matched_fwd, unmatched_fwd = match_forwarder_to_amazon(self.extracted_data, self.remarks)
        has_unmatched = len(unmatched_fwd) > 0

        # 获取已有的目的地（从未匹配的货代标签中提取）
        existing_dest = None
        if unmatched_fwd:
            dests = set(p.get('destination', '') for p in unmatched_fwd if p.get('destination'))
            if len(dests) == 1:
                existing_dest = dests.pop()

        dialog = RemarksDialog(self.root, sku_counts, self.remarks, existing_dest, has_unmatched)
        self.root.wait_window(dialog.dialog)

        if dialog.result is not None:
            self.remarks = dialog.result

            # 如果用户输入了目的地，更新未匹配的货代标签
            if dialog.dest_result and has_unmatched:
                self._apply_dest_to_unmatched(unmatched_fwd, dialog.dest_result)

            # 重新匹配货代标签以更新备注
            if has_forwarder:
                match_forwarder_to_amazon(self.extracted_data, self.remarks)
            self._refresh_tree()
            self.log_window.log(f"已更新 {len(self.remarks)} 个 SKU 的中文备注")

    def _apply_dest_to_unmatched(self, unmatched_fwd, dest):
        """将用户输入的目的地应用到未匹配的货代标签"""
        # 验证格式：3-4个字母 + 1-2个数字（如 TEB9, LAX9, SBD1）
        import re
        if not re.match(r'^[A-Za-z]{3,4}\d{1,2}$', dest):
            messagebox.showwarning("格式错误",
                f"目的地仓库代码格式不正确：{dest}\n正确格式：3-4个字母 + 1-2个数字（如 TEB9、LAX9）")
            return

        dest = dest.upper()
        for fwd in unmatched_fwd:
            if not fwd.get('destination'):
                fwd['destination'] = dest
                self.log_window.log(f"  已为货代标签（第{fwd['page_num']+1}页）设置目的地：{dest}")

    def _process_output(self):
        if not self.extracted_data:
            messagebox.showinfo("提示", "请先导入并提取 PDF 数据。")
            return

        self.output_dir = filedialog.askdirectory(title="选择输出文件夹")
        if not self.output_dir:
            return

        self._set_status("正在处理并生成输出 PDF...")
        self.log_window.log(f"开始处理，输出目录：{self.output_dir}")

        total = len(self.extracted_data) + len(self.exceptions)
        self.progress['maximum'] = max(total, 1)
        self.progress['value'] = 0

        def progress_cb(val):
            self.root.after(0, lambda: setattr(self.progress, 'value', val))

        def do_process():
            try:
                n_pages, n_exc = process_and_output(
                    self.extracted_data, self.exceptions,
                    self.remarks, self.output_dir,
                    progress_callback=progress_cb
                )
                self.root.after(0, lambda: self._on_process_done(n_pages, n_exc))
            except Exception as e:
                self.log_window.log(f"处理出错：{e}")
                self.root.after(0, lambda: messagebox.showerror("错误", str(e)))

        threading.Thread(target=do_process, daemon=True).start()

    def _on_process_done(self, n_pages, n_exc):
        skus = set(p['sku'] for p in self.extracted_data if p.get('sku'))
        grouped = group_and_sort(self.extracted_data, self.remarks)

        self._set_status(
            f"输出完成：生成 {len(grouped)} 个 SKU PDF"
            + (f"，{n_exc} 页异常文件" if n_exc else "")
        )
        self.log_window.log(
            f"输出完成：{len(grouped)} 个分组文件"
            + (f"，{n_exc} 页异常" if n_exc else "")
        )

        msg = f"已生成 {len(grouped)} 个分组 PDF 文件到：\n{self.output_dir}"
        if n_exc:
            msg += f"\n\n另有 {n_exc} 页异常页面归入「异常_需人工核对.pdf」"
        messagebox.showinfo("处理完成", msg)

    def _show_log(self):
        self.log_window.show()

    def _reset(self):
        if not messagebox.askyesno("确认", "确定要清空所有数据并重新开始吗？"):
            return

        self.pdf_files = []
        self.extracted_data = []
        self.exceptions = []
        self.remarks = {}
        self.output_dir = ""

        self.file_listbox.delete(0, tk.END)
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.placeholder.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        self.progress['value'] = 0
        self._set_status("已重置 — 请导入亚马逊外箱标签 PDF 文件")
        self.log_window.clear()
        self.log_window.log("已重置")
