import json
import re
import datetime
import os
import difflib

#测试环境路径
# file_path = r"assets\agent\custom\recognition\tiku.txt"
# log_file_path = r"assets\agent\custom\recognition\search_log.txt"
# file_path = os.path.abspath(file_path)
# log_file_path = os.path.abspath(log_file_path)
# #生产环境路径
# file_path = r"agent\custom\recognition\tiku.txt"
# file_path = os.path.abspath(file_path)
# log_file_path = r"agent\custom\recognition\search_log.txt"
# log_file_path = os.path.abspath(log_file_path)

# 生产环境路径，兼容win和mac
file_path = os.path.join("agent", "custom", "recognition", "tiku.txt")
file_path = os.path.abspath(file_path)
log_file_path = os.path.join("agent", "custom", "recognition", "search_log.txt")
log_file_path = os.path.abspath(log_file_path)

def load_question_bank(file_path):
    """
    加载题库文件并解析为字典格式
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            content = file.read()
            
        # 使用正则表达式匹配问题和答案（兼容冒号与方括号间可能的空格）
        pattern = r'"\s*([^"]+)\s*"\s*:\s*\[\s*([^\]]*?)\s*\]'
        matches = re.findall(pattern, content)
        
        question_bank = {}
        for question, answers in matches:
            # 处理答案格式，正确解析多个答案
            # 使用正则表达式匹配引号内的内容作为单独的答案
            answer_pattern = r'"([^"]+)"'
            answer_list = re.findall(answer_pattern, answers)
            if not answer_list:
                # 如果没有匹配到引号内的内容，尝试直接按逗号分割
                answer_list = [ans.strip().strip('"') for ans in answers.split(',')]
            
            # 处理可能包含逗号的答案
            processed_answers = []
            for ans in answer_list:
                # 检查答案是否包含逗号，如果包含则可能是多个答案合并在一起
                if '，' in ans:
                    # 按中文逗号分割
                    split_answers = [a.strip() for a in ans.split('，')]
                    processed_answers.extend(split_answers)
                else:
                    processed_answers.append(ans)
            
            question_bank[question] = processed_answers
            
        return question_bank
    except Exception as e:
        print(f"加载题库时出错: {e}")
        return {}

def search_answer(question_bank, query, threshold=70):
    """
    在题库中搜索问题并返回答案，并计算可信度
    """
    def normalize_text(s):
        s = s.lower()
        s = re.sub(r"[\s\t\r\n]", "", s)
        s = re.sub(r"[，,、。！？?!：:；;“”\"'（）()【】\[\]\-·—…]", "", s)
        return s

    nq = normalize_text(query)

    for q in question_bank:
        if nq == normalize_text(q):
            return format_answer(question_bank[q]), 100, "精确匹配"

    best_match = None
    best_confidence = 0
    for q in question_bank:
        ratio = difflib.SequenceMatcher(None, nq, normalize_text(q)).ratio()
        similarity = int(round(ratio * 100))
        if similarity > best_confidence:
            best_confidence = similarity
            best_match = q
    if best_match and best_confidence >= threshold:
        return format_answer(question_bank[best_match]), best_confidence, "相似度匹配"
    return "未找到匹配的问题", 0, "低于阈值"

def format_answer(answers):
    """
    格式化答案为指定格式，确保每个答案都有双引号
    """
    # 确保每个答案都被双引号包围
    formatted_answers = []
    for ans in answers:
        # 去除可能存在的引号，然后添加新的双引号
        clean_ans = ans.strip().strip('"')
        formatted_answers.append(f'"{clean_ans}"')
    
    return f"[{','.join(formatted_answers)}]"

# def main():
#     # file_path = "tiku.txt"
#     question_bank = load_question_bank(file_path)
    
#     if not question_bank:
#         print("题库加载失败或为空")
#         return
    
#     print(f"题库加载成功，共有 {len(question_bank)} 个问题")
#     print("输入 'q' 或 'exit' 退出程序")
    
#     while True:
#         query = input("\n请输入要查询的问题: ")
        
#         if query.lower() in ['q', 'exit']:
#             print("程序已退出")
#             break
        
#         result = search_answer(question_bank, query)
#         print(f"答案: {result}")

def log_search_result(query, result, confidence, match_type):
    """
    记录搜索结果到日志文件
    """
    try:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] 问题: {query} | 答案: {result} | 可信度: {confidence}% | 匹配类型: {match_type}\n"
        
        with open(log_file_path, 'a', encoding='utf-8') as log_file:
            log_file.write(log_entry)
            
        return True
    except Exception as e:
        print(f"记录日志时出错: {e}")
        return False

def SearchQuestions(query):
    
    question_bank = load_question_bank(file_path)
    
    if not question_bank:
        print("题库加载失败或为空")
        return
    
    # print(f"题库加载成功，共有 {len(question_bank)} 个问题")
    # print("输入 'q' 或 'exit' 退出程序")
    
    for i in range(1):
        # query = input("\n请输入要查询的问题: ")
        query = query
        if query.lower() in ['q', 'exit']:
            print("程序已退出")
            break
        
        result, confidence, match_type = search_answer(question_bank, query)
        # print(f"答案: {result}")
        # print(f"可信度: {confidence}% ({match_type})")
        # 记录到日志文件
        log_search_result(query, result, confidence, match_type)
        return result, confidence, match_type
# if __name__ == "__main__":
#     main()

__all__ = ['SearchQuestions']