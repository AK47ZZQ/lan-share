using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Windows.Forms;

namespace LanShare
{
    /// <summary>
    /// 视觉样式层。
    ///
    /// 存在的理由：原来每个控件都在 Panel 里手工设 Location/Size/Font/BackColor，
    /// 结果字号互相打架（状态文字比卡片标题还大）、九个按钮长得一模一样、
    /// 每处内边距都是随手拍的数。把「长什么样」抽出来，布局代码只关心「放哪」。
    ///
    /// 只做样式，不做布局。所有数值都是像素，按 96 DPI 设计
    /// （调用方已 SetProcessDPIAware，不会被系统缩放拉伸）。
    /// </summary>
    internal static class Theme
    {
        // ── 颜色 ──
        public static readonly Color Bg          = Color.FromArgb(0xEE, 0xF1, 0xF6);  // 窗口底（比卡片深一档，卡片才浮得起来）
        public static readonly Color Card        = Color.White;                        // 卡片
        public static readonly Color Border      = Color.FromArgb(0xE4, 0xE8, 0xF0);  // 卡片描边
        public static readonly Color FieldBg     = Color.FromArgb(0xF8, 0xFA, 0xFD);  // 只读地址框底

        public static readonly Color TextStrong  = Color.FromArgb(0x1A, 0x1F, 0x2E);  // 大标题
        public static readonly Color TextBody    = Color.FromArgb(0x3C, 0x43, 0x54);  // 正文
        public static readonly Color TextMuted   = Color.FromArgb(0x8A, 0x90, 0xA0);  // 辅助
        public static readonly Color TextFaint   = Color.FromArgb(0xA8, 0xAE, 0xC0);  // 页脚

        public static readonly Color Green       = Color.FromArgb(0x16, 0xA3, 0x4A);
        public static readonly Color Red         = Color.FromArgb(0xDC, 0x26, 0x26);
        public static readonly Color Amber       = Color.FromArgb(0xD9, 0x77, 0x06);
        public static readonly Color Blue        = Color.FromArgb(0x25, 0x63, 0xEB);
        public static readonly Color Gray        = Color.FromArgb(0x9C, 0xA3, 0xB4);

        // ── 字体 ──
        // 字号有明确梯度，不许越级：
        //   H1 20 > Status 12.5 > SectionTitle 9.5 > Body 9 > Meta 8.5 > Footer 8
        private const string Ui = "Microsoft YaHei UI";
        private const string Mono = "Consolas";

        public static readonly Font H1          = new Font(Ui, 20F, FontStyle.Bold);
        public static readonly Font Status      = new Font(Ui, 12.5F, FontStyle.Bold);
        public static readonly Font SectionTitle= new Font(Ui, 9.5F, FontStyle.Bold);
        public static readonly Font Body        = new Font(Ui, 9F, FontStyle.Regular);
        public static readonly Font BodyBold    = new Font(Ui, 9F, FontStyle.Bold);
        public static readonly Font Meta        = new Font(Ui, 8.5F, FontStyle.Regular);
        public static readonly Font Footer      = new Font(Ui, 8F, FontStyle.Regular);
        public static readonly Font Mono9       = new Font(Mono, 9.5F, FontStyle.Regular);
        public static readonly Font MonoBtn     = new Font(Mono, 9F, FontStyle.Regular);

        // ── 间距（8 的倍数，便于对齐）──
        public const int Pad      = 24;   // 窗口左右边距
        public const int CardPad  = 16;   // 卡片内边距
        public const int Gap      = 8;    // 元素间隙
        public const int Radius   = 8;    // 卡片圆角
    }

    /// <summary>绘制辅助。</summary>
    internal static class Draw
    {
        /// <summary>给控件套一个圆角 Region（WinForms 没有原生圆角）。</summary>
        public static void Round(Control c, int radius)
        {
            if (c.Width <= 0 || c.Height <= 0) return;
            var path = RoundedPath(new Rectangle(0, 0, c.Width, c.Height), radius);
            var old = c.Region;
            c.Region = new Region(path);
            if (old != null) old.Dispose();
        }

        public static GraphicsPath RoundedPath(Rectangle r, int radius)
        {
            int d = radius * 2;
            var p = new GraphicsPath();
            if (d <= 0 || d > Math.Min(r.Width, r.Height))
            {
                p.AddRectangle(r);
                return p;
            }
            p.AddArc(r.X, r.Y, d, d, 180, 90);
            p.AddArc(r.Right - d, r.Y, d, d, 270, 90);
            p.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
            p.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
            p.CloseFigure();
            return p;
        }

        /// <summary>画一个状态圆点（用椭圆，不用字符 ● —— 字符大小随字体变，对不齐）。</summary>
        public static void Dot(Graphics g, int x, int y, int size, Color color)
        {
            using (var b = new SolidBrush(color))
                g.FillEllipse(b, x, y, size, size);
        }
    }

    /// <summary>圆角白卡片，自带 1px 描边。</summary>
    internal class Card : Panel
    {
        public Card(int x, int y, int w, int h)
        {
            Location = new Point(x, y);
            Size = new Size(w, h);
            BackColor = Theme.Card;
            DoubleBuffered = true;
        }

        protected override void OnResize(EventArgs e)
        {
            base.OnResize(e);
            Draw.Round(this, Theme.Radius);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            using (var path = Draw.RoundedPath(
                new Rectangle(0, 0, Width - 1, Height - 1), Theme.Radius))
            using (var pen = new Pen(Theme.Border, 1f))
                e.Graphics.DrawPath(pen, path);
        }
    }

    /// <summary>按钮的三种语义。颜色即含义，别混用。</summary>
    internal enum ButtonKind
    {
        /// <summary>主操作：深蓝底白字。一个区域最多一个。</summary>
        Primary,
        /// <summary>普通操作：白底细边框。</summary>
        Secondary,
        /// <summary>破坏性操作：浅红底红字（关闭/清除/重启）。</summary>
        Danger,
        /// <summary>文字链接：无边框无底，点击才显下划线手感。</summary>
        Link,
    }

    internal static class Btn
    {
        /// <summary>
        /// 造按钮。用 FlatStyle.Flat 自绘，不用系统默认灰按钮——
        /// 系统按钮在所有 WinForms 程序里都长一样，是「开发者工具」气质的最大来源。
        /// </summary>
        public static Button Make(string text, ButtonKind kind, int x, int y, int w, int h)
        {
            var b = new Button
            {
                Text = text,
                Location = new Point(x, y),
                Size = new Size(w, h),
                Font = Theme.Body,
                FlatStyle = FlatStyle.Flat,
                TextAlign = ContentAlignment.MiddleCenter,
                Cursor = Cursors.Hand,
                UseVisualStyleBackColor = false,
                TabStop = kind != ButtonKind.Link,
            };

            b.FlatAppearance.BorderSize = 1;
            // 焦点框是 WinForms 最难看的默认行为之一（粗蓝框），压细
            b.FlatAppearance.BorderColor = Theme.Border;

            switch (kind)
            {
                case ButtonKind.Primary:
                    b.BackColor = Theme.Blue;
                    b.ForeColor = Color.White;
                    b.FlatAppearance.BorderColor = Theme.Blue;
                    b.FlatAppearance.MouseOverBackColor = Color.FromArgb(0x1D, 0x4E, 0xD8);
                    b.FlatAppearance.MouseDownBackColor = Color.FromArgb(0x1E, 0x40, 0xAF);
                    break;

                case ButtonKind.Danger:
                    b.BackColor = Color.FromArgb(0xFE, 0xF2, 0xF2);
                    b.ForeColor = Theme.Red;
                    b.FlatAppearance.BorderColor = Color.FromArgb(0xFB, 0xD5, 0xD5);
                    b.FlatAppearance.MouseOverBackColor = Color.FromArgb(0xFE, 0xE2, 0xE2);
                    b.FlatAppearance.MouseDownBackColor = Color.FromArgb(0xFD, 0xD0, 0xD0);
                    break;

                case ButtonKind.Link:
                    b.BackColor = Theme.Card;
                    b.ForeColor = Theme.Blue;
                    b.FlatAppearance.BorderSize = 0;
                    b.FlatAppearance.MouseOverBackColor = Theme.Card;
                    b.FlatAppearance.MouseDownBackColor = Theme.Card;
                    break;

                default: // Secondary
                    b.BackColor = Color.White;
                    b.ForeColor = Theme.TextBody;
                    b.FlatAppearance.MouseOverBackColor = Color.FromArgb(0xF2, 0xF5, 0xFA);
                    b.FlatAppearance.MouseDownBackColor = Color.FromArgb(0xE8, 0xED, 0xF6);
                    break;
            }

            return b;
        }

        /// <summary>
        /// 换一个按钮的语义样式。
        ///
        /// 为什么要这个：同一个按钮的身份会变 —— 「启动局域网服务」开着的时候
        /// 就是「关闭局域网」，语义从「主操作」变成「破坏性操作」，颜色必须跟着变。
        /// 光改 Text 不改色，用户会以为还是那个安全按钮。
        /// </summary>
        public static void Restyle(Button b, ButtonKind kind)
        {
            if (b == null) return;

            switch (kind)
            {
                case ButtonKind.Primary:
                    b.BackColor = Theme.Blue;
                    b.ForeColor = Color.White;
                    b.FlatAppearance.BorderSize = 1;
                    b.FlatAppearance.BorderColor = Theme.Blue;
                    b.FlatAppearance.MouseOverBackColor = Color.FromArgb(0x1D, 0x4E, 0xD8);
                    b.FlatAppearance.MouseDownBackColor = Color.FromArgb(0x1E, 0x40, 0xAF);
                    break;

                case ButtonKind.Danger:
                    b.BackColor = Color.FromArgb(0xFE, 0xF2, 0xF2);
                    b.ForeColor = Theme.Red;
                    b.FlatAppearance.BorderSize = 1;
                    b.FlatAppearance.BorderColor = Color.FromArgb(0xFB, 0xD5, 0xD5);
                    b.FlatAppearance.MouseOverBackColor = Color.FromArgb(0xFE, 0xE2, 0xE2);
                    b.FlatAppearance.MouseDownBackColor = Color.FromArgb(0xFD, 0xD0, 0xD0);
                    break;

                case ButtonKind.Link:
                    b.BackColor = Theme.Card;
                    b.ForeColor = Theme.Blue;
                    b.FlatAppearance.BorderSize = 0;
                    b.FlatAppearance.MouseOverBackColor = Theme.Card;
                    b.FlatAppearance.MouseDownBackColor = Theme.Card;
                    break;

                default:
                    b.BackColor = Color.White;
                    b.ForeColor = Theme.TextBody;
                    b.FlatAppearance.BorderSize = 1;
                    b.FlatAppearance.BorderColor = Theme.Border;
                    b.FlatAppearance.MouseOverBackColor = Color.FromArgb(0xF2, 0xF5, 0xFA);
                    b.FlatAppearance.MouseDownBackColor = Color.FromArgb(0xE8, 0xED, 0xF6);
                    break;
            }
        }

        /// <summary>只读地址框：看出来是内容不是输入框，但能选中复制。</summary>
        public static TextBox Address(int x, int y, int w, int h)
        {
            return new TextBox
            {
                Location = new Point(x, y),
                Size = new Size(w, h),
                ReadOnly = true,
                BorderStyle = BorderStyle.None,
                BackColor = Theme.FieldBg,
                ForeColor = Theme.TextBody,
                Font = Theme.Mono9,
            };
        }
    }
}
