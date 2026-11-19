import { useEffect,useRef,type PropsWithChildren } from 'react'
import { NavLink,useLocation } from 'react-router-dom'
import { BookOpen,Brain,Camera,House,Library,ListChecks,Sprout,MessageCircle,Settings,Plus,X } from 'lucide-react'
import { useAppState } from '../context/AppState'
const items=[['/','首页',House],['/summaries','记忆',BookOpen],['/grow','成长',Sprout],['/list','待办',ListChecks],['/second-brain','第二大脑',Brain],['/agent','学习助手',MessageCircle]] as const
export function Workspace({children}:PropsWithChildren){
 const {name,storageError}=useAppState(),location=useLocation()
 return <div className="workspace"><aside className="sidebar"><NavLink className="brand" to="/"><span className="brand-mark">M</span><span>MetaMind<span className="brand-sub">YOUR PERSONAL MEMORY</span></span></NavLink><p className="nav-caption">我的工作空间</p><nav aria-label="主导航">{items.map(([to,label,Icon])=><NavLink key={to} to={to} end={to==='/'} className={({isActive})=>isActive?'active':''}><Icon size={21}/><span>{label}</span></NavLink>)}</nav><div className="sidebar-extra"><NavLink to="/capture"><Plus size={19}/>新建记录</NavLink><NavLink to="/facts"><Brain size={19}/>我的事实</NavLink><NavLink to="/settings"><Settings size={19}/>设置与数据</NavLink></div><div className="sidebar-profile"><span className="avatar-small">{name.slice(0,1)}</span><div>{name}<small>独立个人工作空间</small></div></div></aside><main className="workspace-main"><header className="workspace-bar"><span className="top-context">PERSONAL INTELLIGENCE <span>/</span> 让每一次记录，产生价值</span><div><NavLink to="/settings" className="top-settings" aria-label="设置与数据"><Settings size={18}/></NavLink><NavLink to="/capture" className="primary compact"><Plus size={17}/>新建记录</NavLink></div></header>{storageError&&<p role="alert" className="error">{storageError}</p>}<div key={location.pathname} className="workspace-content">{children}</div></main><nav className="mobile-nav" aria-label="移动导航">{items.map(([to,label,Icon])=><NavLink key={to} to={to} end={to==='/'} className={({isActive})=>isActive?'active':''} aria-label={label}><Icon size={21}/><span>{label==='学习助手'?'助手':label==='第二大脑'?'大脑':label}</span></NavLink>)}</nav></div>
}
export function Modal({title,close,children}:PropsWithChildren<{title:string;close:()=>void}>){
 const ref=useRef<HTMLDialogElement>(null)
 useEffect(()=>{const dialog=ref.current;dialog?.showModal();return()=>dialog?.close()},[])
 return <dialog ref={ref} className="modal" onCancel={close} onClick={e=>{if(e.target===e.currentTarget)close()}}><div className="modal-inner"><header><h2>{title}</h2><button className="icon" onClick={close} aria-label="关闭"><X size={21}/></button></header>{children}</div></dialog>
}
export function Heading({title,subtitle,children}:PropsWithChildren<{title:string;subtitle?:string}>){return <header className="heading"><div><h1>{title}</h1>{subtitle&&<p>{subtitle}</p>}</div>{children}</header>}
export function Empty({children}:PropsWithChildren){return <div className="empty"><span>✦</span><p>{children}</p></div>}
