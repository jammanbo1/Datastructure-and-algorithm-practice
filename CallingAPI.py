// 기존 runQuick() 함수를 덮어씌웁니다.
async function runQuick(){
  const text = $('qText').value.trim();
  if(!text){ toast('들은 말을 먼저 적거나 말해 주세요.'); return; }
  
  stopMic();
  
  // UX 개선: 로딩 상태 표시
  const btn = $('qRun');
  const originalBtnText = btn.textContent;
  btn.textContent = 'AI가 맥락을 분석하고 있어요...';
  btn.disabled = true;

  try {
    // 파이썬 로컬 서버(FastAPI)로 요청 보내기
    const response = await fetch('http://127.0.0.1:8000/extract-info', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: text })
    });

    if (!response.ok) throw new Error('서버 응답 오류');
    
    const data = await response.json();
    console.log("AI 추출 결과:", data);

    // AI가 준 JSON 데이터를 앱의 형식(lastSug)에 맞게 변환
    lastSug = [];
    if(data.deposit != null) lastSug.push({q:'ctx', k:'deposit', v:data.deposit, where:'공통 정보', show:'내 보증금 ' + won(data.deposit), quote:text});
    if(data.rent != null) lastSug.push({q:'ctx', k:'rent', v:data.rent, where:'공통 정보', show:'월세 ' + won(data.rent), quote:text});
    if(data.mort != null) {
      lastSug.push({q:'q5', k:'has', v: data.mort === 0 ? 'none' : 'yes', where:'권리관계 확인 · 근저당 확인', show:'근저당이 있나요? → ' + (data.mort === 0 ? '없어요' : '있어요'), quote:text});
      if(data.mort > 0) lastSug.push({q:'q5', k:'mort', v:data.mort, where:'권리관계 확인 · 근저당 확인', show:'채권최고액 ' + won(data.mort), quote:text});
    }

    // 결과 화면 그리기 (기존 로직 유지)
    let h = URGENT.test(text) ? '<div style="margin-top:16px">' + urgentBox() + '</div>' : '';
    if(!lastSug.length) h += '<p class="sh-empty">찾은 답이 없어요. 다시 말해보세요.</p>';
    else h += '<p class="sec-t">AI가 찾은 답 ' + lastSug.length + '개<small>맞는 것만 남겨 주세요</small></p><div class="card">' +
      lastSug.map((x,i)=>'<label class="sug"><input type="checkbox" checked data-i="' + i + '"><span><span class="sw">' + esc(x.where) + '</span><span class="sv">' + esc(x.show) + '</span>' +
        (x.was ? '<span class="was">지금 답 "' + esc(x.was) + '"에서 바뀌어요</span>' : '') + '</span></label>').join('') +
      '</div><button type="button" class="btn" id="qApply">고른 답 채우기</button>';
    
    $('qOut').innerHTML = h;
    $('qOut').scrollIntoView({behavior:'smooth', block:'start'});

  } catch (error) {
    toast('AI 분석 중 문제가 발생했습니다. 다시 시도해 주세요.');
    console.error(error);
  } finally {
    // 버튼 상태 복구
    btn.textContent = originalBtnText;
    btn.disabled = false;
  }
}
