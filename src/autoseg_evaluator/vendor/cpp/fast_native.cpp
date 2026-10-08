// Experimental continuous planar metric engine. No rasterization or sampling.
// Assistant-authored; binary64 arithmetic, not certified exact predicates.
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <vector>

#ifdef _WIN32
#define EXPORT extern "C" __declspec(dllexport)
#else
#define EXPORT extern "C"
#endif

namespace {
constexpr double eps=std::numeric_limits<double>::epsilon();
struct V { double x,y; };
V operator-(V a,V b){return {a.x-b.x,a.y-b.y};}
V operator+(V a,V b){return {a.x+b.x,a.y+b.y};}
V operator*(V a,double t){return {a.x*t,a.y*t};}
double dot(V a,V b){return a.x*b.x+a.y*b.y;}
double cross(V a,V b){return a.x*b.y-a.y*b.x;}
V point(const double* p){return {p[0],p[1]};}
double point_segment(V p,V a,V b){
    V v=b-a;double vv=dot(v,v),t=vv?std::clamp(dot(p-a,v)/vv,0.,1.):0.;
    V d=(p-a)-v*t;return std::hypot(d.x,d.y);
}
struct F {
    double x,y,vx,vy;
    double value(double t)const {return std::hypot(x+vx*t,y+vy*t);}
    double A()const{return vx*vx+vy*vy;}
    double B()const{return 2*(x*vx+y*vy);}
    double C()const{return x*x+y*y;}
};
struct Piece {double lo,hi;F f;double weight;double min_d=0,max_d=0;};
using Env=std::vector<Piece>;

void roots(double a,double b,double c,double lo,double hi,std::vector<double>& cuts){
    if(a==0){if(b){double t=-c/b;if(t>lo&&t<hi)cuts.push_back(t);}return;}
    double disc=std::fma(b,b,-4*a*c);
    if(disc<0)return;
    double s=std::sqrt(disc),q=-.5*(b+std::copysign(s,b));
    if(q==0){double t=-b/(2*a);if(t>lo&&t<hi)cuts.push_back(t);return;}
    for(double t:{q/a,c/q})if(t>lo&&t<hi)cuts.push_back(t);
}

Env feature(V p,V v,V a,V b){
    V w=b-a,r=p-a;double w2=dot(w,w);
    auto endpoint=[&](V c){V d=p-c;return F{d.x,d.y,v.x,v.y};};
    if(!w2)return {{0,1,endpoint(a),0}};
    double u0=dot(r,w)/w2,u1=dot(v,w)/w2;
    std::vector<double> cuts{0,1};
    if(u1)for(double t:{-u0/u1,(1-u0)/u1})if(t>0&&t<1)cuts.push_back(t);
    std::sort(cuts.begin(),cuts.end());Env result;
    for(size_t i=1;i<cuts.size();++i){
        double lo=cuts[i-1],hi=cuts[i],u=u0+u1*(lo+hi)/2;
        F f=u<0?endpoint(a):u>1?endpoint(b):F{cross(w,r)/std::sqrt(w2),0,cross(w,v)/std::sqrt(w2),0};
        if(hi>lo)result.push_back({lo,hi,f,0});
    }
    return result;
}

Env merge(const Env& a,const Env& b){
    Env result;result.reserve(a.size()+b.size());size_t i=0,j=0;
    while(i<a.size()&&j<b.size()){
        const Piece& p=a[i];const Piece& q=b[j];
        double lo=std::max(p.lo,q.lo),hi=std::min(p.hi,q.hi);
        if(lo<hi){
            std::vector<double> cuts{lo,hi};
            roots(p.f.A()-q.f.A(),p.f.B()-q.f.B(),p.f.C()-q.f.C(),lo,hi,cuts);
            std::sort(cuts.begin(),cuts.end());
            for(size_t k=1;k<cuts.size();++k){
                double x=cuts[k-1],y=cuts[k];if(x==y)continue;
                F f=p.f.value((x+y)/2)<=q.f.value((x+y)/2)?p.f:q.f;
                result.push_back({x,y,f,0});
            }
        }
        if(p.hi<=q.hi)++i;if(q.hi<=p.hi)++j;
    }
    return result;
}

double coverage(const Piece& p,double tau){
    if(p.weight>0){if(tau>=p.max_d)return p.hi-p.lo;if(tau<p.min_d)return 0.;}
    double a=p.f.A();if(!a)return p.f.value(0)<=tau?p.hi-p.lo:0.;
    double centre=-(p.f.x*p.f.vx+p.f.y*p.f.vy)/a;
    double c=cross({p.f.x,p.f.y},{p.f.vx,p.f.vy});
    double h2=c*c/a,delta=tau*tau-h2;
    if(delta<0)return 0.;
    double radius=std::sqrt(delta/a);
    return std::max(0.,std::min(p.hi,centre+radius)-std::max(p.lo,centre-radius));
}

double integral(const Piece& p){
    double a=p.f.A();if(!a)return (p.hi-p.lo)*p.f.value(0);
    double speed=std::sqrt(a),centre=-(p.f.x*p.f.vx+p.f.y*p.f.vy)/a;
    double h=std::abs(cross({p.f.x,p.f.y},{p.f.vx,p.f.vy}))/speed;
    double u=speed*(p.lo-centre),v=speed*(p.hi-centre);
    // Avoid cancellation in an antiderivative difference over very short pieces.
    if(v-u<.01*std::max({std::abs(u),std::abs(v),h,1e-300})){
        static const double x[4]={.1834346424956498,.5255324099163290,.7966664774136267,.9602898564975363};
        static const double w[4]={.3626837833783620,.3137066458778873,.2223810344533745,.1012285362903763};
        double mid=(p.lo+p.hi)/2,half=(p.hi-p.lo)/2,sum=0;
        for(int i=0;i<4;++i)sum+=w[i]*(p.f.value(mid-half*x[i])+p.f.value(mid+half*x[i]));
        return half*sum;
    }
    auto primitive=[&](double t){return .5*(t*std::hypot(t,h)+(h?h*h*std::asinh(t/h):0.));};
    return (primitive(v)-primitive(u))/speed;
}

double cdf(const Env& distribution,double t){
    double sum=0,correction=0;
    for(const auto& p:distribution){double v=p.weight*coverage(p,t)-correction,next=sum+v;correction=(next-sum)-v;sum=next;}
    return sum;
}
double quantile(const Env& distribution,double mass,double hd,double tol){
    if(cdf(distribution,0)>=mass)return 0;
    double lo=0,hi=hd;
    for(int i=0;i<100&&hi-lo>tol;++i){double mid=(lo+hi)/2;if(cdf(distribution,mid)>=mass)hi=mid;else lo=mid;}
    return (lo+hi)/2;
}
struct Quantile {double value,gap;};
Quantile guarded_quantile(const Env& distribution,double mass,double guard,double hd){
    double lower_mass=mass-guard,upper_mass=mass+guard;
    if(cdf(distribution,0)>=upper_mass)return {0,0};
    double lo=0,hi=hd;
    for(int i=0;i<100&&hi-lo>1e-9;++i){
        double mid=(lo+hi)/2,m=cdf(distribution,mid);
        if(m<lower_mass)lo=mid;
        else if(m>=upper_mass)hi=mid;
        else{
            double qlo=quantile(distribution,lower_mass,hd,1e-9),qhi=quantile(distribution,upper_mass,hd,1e-9);
            return {(qlo+qhi)/2,qhi-qlo};
        }
    }
    return {(lo+hi)/2,hi-lo};
}
}

// groups: source start/count, target start/count, multiplicity. Output:
// length, HD100, mean, median, HD95, pieces, candidate segments, median gap,
// HD95 gap, then APL at each tolerance. Fallback flags refer to ORIGINAL edges.
EXPORT int ncm_direction(const double* source,int64_t ns,const double* target,int64_t nt,
    const int64_t* groups,int64_t ng,const double* taus,int64_t ntau,double error,
    double* output,uint8_t* fallback){
    try{
        if(ns<1||nt<1||ng<1||ntau<1||error<=0)return 1;
        Env distribution;double length=0,area=0,hd=0,candidates=0;
        std::vector<double> apl(ntau,0.);
        std::fill(fallback,fallback+ns*ntau,0);
        for(int64_t g=0;g<ng;++g){
            auto group=groups+5*g;int64_t s0=group[0],sn=group[1],t0=group[2],tn=group[3];double count=double(group[4]);
            if(s0<0||sn<0||s0+sn>ns||t0<0||tn<1||t0+tn>nt||count<1)return 2;
            for(int64_t si=s0;si<s0+sn;++si){
                V p=point(source+4*si),end=point(source+4*si+2),v=end-p,mid=p+v*.5;
                double speed=std::hypot(v.x,v.y);if(!speed)continue;
                double nearest=INFINITY;int64_t pick=t0;
                for(int64_t ti=t0;ti<t0+tn;++ti){double d=point_segment(mid,point(target+4*ti),point(target+4*ti+2));if(d<nearest){nearest=d;pick=ti;}}
                V aa=point(target+4*pick),bb=point(target+4*pick+2);
                double upper=std::max(point_segment(p,aa,bb),point_segment(end,aa,bb));
                // Conservative roundoff padding for candidate pruning only;
                // never changes the APL metric threshold.
                double scale=std::max({1.,std::abs(p.x),std::abs(p.y),std::abs(end.x),std::abs(end.y),upper});
                double guard=4096*eps*scale;
                std::vector<Env> layers;
                for(int64_t ti=t0;ti<t0+tn;++ti){
                    V a=point(target+4*ti),b=point(target+4*ti+2);
                    double dx=std::max({0.,std::min(a.x,b.x)-std::max(p.x,end.x),std::min(p.x,end.x)-std::max(a.x,b.x)});
                    double dy=std::max({0.,std::min(a.y,b.y)-std::max(p.y,end.y),std::min(p.y,end.y)-std::max(a.y,b.y)});
                    if(std::hypot(dx,dy)>upper+guard)continue;
                    layers.push_back(feature(p,v,a,b));++candidates;
                }
                while(layers.size()>1){
                    std::vector<Env> next;next.reserve((layers.size()+1)/2);
                    for(size_t i=0;i<layers.size();i+=2)next.push_back(i+1<layers.size()?merge(layers[i],layers[i+1]):std::move(layers[i]));
                    layers=std::move(next);
                }
                if(layers.empty())return 3;
                auto& env=layers[0];length+=speed*count;
                for(auto& item:env){
                    item.weight=speed*count;
                    item.max_d=std::max(item.f.value(item.lo),item.f.value(item.hi));
                    double aa=item.f.A(),centre=aa?-(item.f.x*item.f.vx+item.f.y*item.f.vy)/aa:0;
                    item.min_d=item.f.value(std::clamp(centre,item.lo,item.hi));
                    hd=std::max(hd,item.max_d);
                    area+=item.weight*integral(item);distribution.push_back(item);
                }
                if(distribution.size()>5000000)return 4;
                for(int64_t k=0;k<ntau;++k){
                    double matched=0;bool fragile=false;
                    for(const auto& item:env){
                        matched+=coverage(item,taus[k]);
                        double d0=item.f.value(item.lo),d1=item.f.value(item.hi);
                        double a=item.f.A(),centre=a?-(item.f.x*item.f.vx+item.f.y*item.f.vy)/a:0;
                        double minimum=item.f.value(std::clamp(centre,item.lo,item.hi));
                        // Threshold plateaus, tangencies and endpoint coincidences
                        // go to the original-coordinate Decimal reference.
                        if(std::abs(minimum-taus[k])<=guard||std::abs(d0-taus[k])<=guard||std::abs(d1-taus[k])<=guard)fragile=true;
                    }
                    if(fragile)fallback[si*ntau+k]=1;
                    else apl[k]+=speed*count*std::clamp(1-matched,0.,1.);
                }
            }
        }
        if(!(length>0)||!std::isfinite(area)||!std::isfinite(hd))return 5;
        output[0]=length;output[1]=hd;output[2]=area/length;output[5]=double(distribution.size());output[6]=candidates;
        // Quantile bracket against a small CDF mass perturbation catches
        // non-unique/ill-conditioned plateaus, without selecting silently.
        double massguard=(64*distribution.size()+256)*eps*length;
        for(int k=0;k<2;++k){
            double p=k?.95:.5,mass=p*length;
            auto q=guarded_quantile(distribution,mass,massguard,hd);
            output[7+k]=q.gap;output[3+k]=q.value;
        }
        for(int64_t k=0;k<ntau;++k)output[9+k]=apl[k];
        return 0;
    }catch(...){return 9;}
}
