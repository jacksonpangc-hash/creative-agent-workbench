import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
export default function Markdown({text}:{text:string}){return <div className="markdown-body"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{a:({children,href})=><a href={href} target="_blank" rel="noreferrer">{children}</a>,img:()=>null}}>{text}</ReactMarkdown></div>}
